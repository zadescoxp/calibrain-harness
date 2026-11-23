// Calibrain Harness desktop shell.
//
// Owns the Python backend (ctb.server, frozen with PyInstaller) for its whole life:
//   start on a free 127.0.0.1 port -> wait for /api/health -> show the web UI in a native window
//   -> on quit, close the backend's stdin (its lifeline: it shuts down cleanly), then hard-kill after a grace period.
// If this process dies abruptly the pipe closes anyway, so the backend never outlives the app.
// Exit code 75 from the backend = "restart me" (venue / symbol changes); it comes back on the same port.
"use strict";

const { app, BrowserWindow, Menu, shell, dialog, nativeTheme, session } = require("electron");
const { spawn, execFile } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const http = require("node:http");

const APP_TITLE = "Calibrain Harness";
const RESTART_EXIT_CODE = 75;
const PREFERRED_PORT = 18800;
const HEALTH_TIMEOUT_MS = 180_000;
const STOP_GRACE_MS = 8_000;
const IS_WIN = process.platform === "win32";
const IS_MAC = process.platform === "darwin";

app.setName(APP_TITLE);
nativeTheme.themeSource = "dark";

// One instance only: two backends on one SQLite DB and one Bybit account would fight.
if (!app.requestSingleInstanceLock()) {
  app.quit();
  return;
}

// ---------------------------------------------------------------- paths
const userData = app.getPath("userData"); // macOS ~/Library/Application Support/Calibrain Harness, Windows %APPDATA%\Calibrain Harness
const HOME = path.join(userData, "harness"); // .env (keys), data/ctb.db, KILL: owner-only
const LOG_DIR = path.join(HOME, "logs");
const LOG_FILE = path.join(LOG_DIR, "backend.log");
const STATE_FILE = path.join(userData, "desktop-state.json");

function backendCommand() {
  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, "backend");
    return { cmd: path.join(dir, IS_WIN ? "ctb-backend.exe" : "ctb-backend"), args: [], cwd: dir,
             ui: path.join(process.resourcesPath, "ui") };
  }
  // `npm start` from desktop/: run the source tree with the repo's venv
  const repo = path.resolve(__dirname, "..", "..");
  const py = process.env.CTB_PYTHON || (IS_WIN ? path.join(repo, ".venv", "Scripts", "python.exe") : path.join(repo, ".venv", "bin", "python"));
  return { cmd: py, args: ["-m", "ctb.server"], cwd: repo, ui: path.join(repo, "web-next", "out") };
}

// ---------------------------------------------------------------- small persisted state (port, window bounds)
function readState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, "utf8")); } catch { return {}; }
}
function writeState(patch) {
  try { fs.writeFileSync(STATE_FILE, JSON.stringify({ ...readState(), ...patch }, null, 2)); } catch { /* not fatal */ }
}

// ---------------------------------------------------------------- ports
function portFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.listen({ port, host: "127.0.0.1", exclusive: true }, () => srv.close(() => resolve(true)));
  });
}
function ephemeralPort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once("error", reject);
    srv.listen({ port: 0, host: "127.0.0.1" }, () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
  });
}
// A stable port keeps the UI's origin (and so its saved layout in localStorage) the same across launches.
async function pickPort(avoid) {
  const saved = Number(readState().port) || PREFERRED_PORT;
  for (const p of [saved, PREFERRED_PORT]) {
    if (p !== avoid && (await portFree(p))) return p;
  }
  const p = await ephemeralPort();
  writeState({ port: p });
  return p;
}

// ---------------------------------------------------------------- backend lifecycle
let backend = null; // { child, port, exited: Promise }
let port = 0;
let quitting = false;
let restarting = false;
let starting = false;
let logTail = [];
let logStream = null;

function openLog() {
  fs.mkdirSync(LOG_DIR, { recursive: true, mode: 0o700 });
  try { if (fs.statSync(LOG_FILE).size > 5 * 1024 * 1024) fs.renameSync(LOG_FILE, LOG_FILE + ".1"); } catch { /* none yet */ }
  logStream = fs.createWriteStream(LOG_FILE, { flags: "a", mode: 0o600 });
}
function onOutput(buf) {
  const text = buf.toString("utf8");
  logStream?.write(text);
  for (const line of text.split(/\r?\n/)) {
    if (line.trim() && !line.includes("aiohttp.access")) logTail.push(line);
  }
  if (logTail.length > 40) logTail = logTail.slice(-40);
}

// Never let a key the user exported in a shell override the app's own .env.
function backendEnv(p, ui) {
  const env = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (!/^(CTB_|BYBIT_|KAIROS_|TYPESAFE_|PYTHON)/.test(k)) env[k] = v;
  }
  return { ...env, CTB_HOME: HOME, CTB_HOST: "127.0.0.1", CTB_PORT: String(p), CTB_UI_DIR: ui, CTB_SUPERVISED: "1",
           PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1" };
}

function startBackend(p) {
  const { cmd, args, cwd, ui } = backendCommand();
  if (!fs.existsSync(cmd)) throw new Error(`backend not found at ${cmd}`);
  logStream?.write(`\n==== ${new Date().toISOString()} starting backend on 127.0.0.1:${p}\n`);
  const child = spawn(cmd, args, { cwd, env: backendEnv(p, ui), stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
  child.stdout.on("data", onOutput);
  child.stderr.on("data", onOutput);
  child.stdin.on("error", () => { /* backend already gone */ });
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  child.once("error", (err) => onOutput(Buffer.from(`failed to start backend: ${err.message}\n`)));
  backend = { child, port: p, exited };
  exited.then(({ code, signal }) => onBackendExit(child, code, signal));
  return backend;
}

function getJSON(url, timeoutMs = 2000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.on("data", (d) => (body += d));
      res.on("end", () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

// Healthy = /api/health answers from *our* child (same PID), not some other server on the port.
async function waitHealthy(b) {
  const t0 = Date.now();
  let dead = false;
  b.exited.then(() => (dead = true));
  while (Date.now() - t0 < HEALTH_TIMEOUT_MS) {
    if (dead) return false;
    try {
      const h = await getJSON(`http://127.0.0.1:${b.port}/api/health`);
      if (h && h.app === "calibrain-harness" && h.pid === b.child.pid) return true;
    } catch { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

async function launchBackend() {
  starting = true;
  try { return await tryLaunch(); } finally { starting = false; }
}
async function tryLaunch() {
  for (let attempt = 0; attempt < 3; attempt++) {
    if (quitting) return false;
    port = await pickPort(attempt ? port : 0);
    setSplash("STARTING", `starting the engine on 127.0.0.1:${port}…`);
    const b = startBackend(port);
    if (await waitHealthy(b)) {
      writeState({ port });
      return true;
    }
    if (quitting) return false;
    const bindError = logTail.some((l) => /address already in use|Errno 48|Errno 98|10048/i.test(l));
    await stopBackend();
    if (!bindError) return false; // a real failure: show it, do not loop
    port = 0; // lost a race for the port: try another one
  }
  return false;
}

function onBackendExit(child, code, signal) {
  if (!backend || backend.child !== child) return;
  backend = null;
  logStream?.write(`==== backend exited (code ${code}, signal ${signal})\n`);
  if (quitting || restarting || starting) return;
  if (code === RESTART_EXIT_CODE) {
    // asked for by the UI (venue / symbol change): the page shows its own reconnect state meanwhile
    restartBackend({ keepPage: true });
    return;
  }
  showFailure(`the engine stopped unexpectedly (exit ${code ?? signal}).`);
}

function killTree(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  if (IS_WIN) execFile("taskkill", ["/pid", String(child.pid), "/T", "/F"], () => {});
  else try { child.kill("SIGKILL"); } catch { /* gone */ }
}

// Graceful: close the lifeline (the backend stops its engine, closes the DB, exits); escalate if it hangs.
async function stopBackend() {
  const b = backend;
  if (!b) return;
  backend = null;
  const { child, exited } = b;
  if (child.exitCode !== null || child.signalCode !== null) return;
  try { child.stdin.end(); } catch { /* already closed */ }
  const timeout = (ms) => new Promise((r) => setTimeout(() => r("timeout"), ms));
  if ((await Promise.race([exited, timeout(STOP_GRACE_MS)])) !== "timeout") return;
  if (!IS_WIN) { try { child.kill("SIGTERM"); } catch { /* gone */ } }
  if ((await Promise.race([exited, timeout(2000)])) !== "timeout") return;
  killTree(child);
  await Promise.race([exited, timeout(3000)]);
}

async function restartBackend({ keepPage = false } = {}) {
  if (restarting) return;
  restarting = true;
  try {
    await stopBackend();
    const oldPort = port;
    if (!keepPage) loadSplash();
    const ok = await launchBackend();
    if (!ok) return showFailure("the engine did not become healthy.");
    if (!keepPage || port !== oldPort) loadApp();
  } finally {
    restarting = false;
  }
}

// ---------------------------------------------------------------- window
let win = null;
let splashShown = false;

function createWindow() {
  const st = readState();
  const b = st.bounds || {};
  win = new BrowserWindow({
    title: APP_TITLE,
    width: b.width || 1440,
    height: b.height || 900,
    x: b.x, y: b.y,
    minWidth: 720,
    minHeight: 480,
    backgroundColor: "#0a0a0a",
    show: false,
    autoHideMenuBar: IS_WIN, // Alt shows it; keeps the terminal look edge to edge
    icon: IS_MAC ? undefined : path.join(__dirname, "..", "build", "icon.png"),
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false,
                      backgroundThrottling: false }, // live feeds keep flowing when the window is in the back
  });
  if (st.maximized) win.maximize();
  win.once("ready-to-show", () => win.show());
  win.on("page-title-updated", (e) => { e.preventDefault(); win.setTitle(APP_TITLE); });
  const saveBounds = () => { if (win && !win.isMaximized() && !win.isMinimized()) writeState({ bounds: win.getBounds() }); };
  win.on("resized", saveBounds);
  win.on("moved", saveBounds);
  win.on("close", () => writeState({ maximized: win.isMaximized() }));
  win.on("closed", () => { win = null; });

  const wc = win.webContents;
  // Only the local backend renders in this window; everything else opens in the user's browser.
  wc.setWindowOpenHandler(({ url }) => { openExternal(url); return { action: "deny" }; });
  wc.on("will-navigate", (e, url) => {
    if (url.startsWith("calibrain://")) { e.preventDefault(); splashAction(url.slice("calibrain://".length)); return; }
    if (!isAppUrl(url)) { e.preventDefault(); openExternal(url); }
  });
  wc.on("render-process-gone", (_e, d) => { if (!quitting && d.reason !== "clean-exit") wc.reload(); });
  loadSplash();
}

function isAppUrl(url) {
  try { const u = new URL(url); return u.hostname === "127.0.0.1" && Number(u.port) === port; } catch { return false; }
}
function openExternal(url) {
  if (/^https?:\/\//i.test(url) && !isAppUrl(url)) shell.openExternal(url);
}

function loadSplash() {
  if (!win) return;
  splashShown = true;
  win.loadFile(path.join(__dirname, "splash.html"));
}
function loadApp() {
  if (!win) return;
  splashShown = false;
  win.loadURL(`http://127.0.0.1:${port}/`);
}
function setSplash(title, msg, log = "", isError = false) {
  if (!win || !splashShown) return;
  const js = `window.setStatus && window.setStatus(${JSON.stringify(title)}, ${JSON.stringify(msg)}, ${JSON.stringify(log)}, ${isError})`;
  const run = () => win && win.webContents.executeJavaScript(js).catch(() => {});
  if (win.webContents.isLoading()) win.webContents.once("did-finish-load", run); else run();
}
function showFailure(msg) {
  if (quitting) return;
  if (!splashShown) loadSplash();
  setSplash("ENGINE ERROR", msg, logTail.slice(-25).join("\n"), true);
}
function splashAction(action) {
  if (action === "restart") restartBackend();
  else if (action === "logs") shell.openPath(LOG_DIR);
  else if (action === "quit") app.quit();
}

function buildMenu() {
  const template = [
    ...(IS_MAC ? [{ role: "appMenu" }] : []),
    {
      label: "File",
      submenu: [
        { label: "Open Data Folder", click: () => shell.openPath(HOME) },
        { label: "Open Log Folder", click: () => shell.openPath(LOG_DIR) },
        { type: "separator" },
        { label: "Restart Engine", click: () => restartBackend() },
        { label: "Open in Browser", click: () => port && shell.openExternal(`http://127.0.0.1:${port}/`) },
        { type: "separator" },
        IS_MAC ? { role: "close" } : { role: "quit" },
      ],
    },
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "reload" }, { role: "forceReload" }, { role: "toggleDevTools" }, { type: "separator" },
        { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" }, { type: "separator" }, { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
    { role: "help", submenu: [{ label: "About Calibrain Harness", click: showAbout }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}
function showAbout() {
  dialog.showMessageBox(win, {
    type: "info", title: APP_TITLE, message: `${APP_TITLE} ${app.getVersion()}`,
    detail: `Research terminal on demo accounts: orders only go to Bybit Demo Trading or Bybit Testnet.\n\nData and keys: ${HOME}\nEngine: 127.0.0.1:${port}`,
  });
}

// ---------------------------------------------------------------- app events
app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

app.whenReady().then(async () => {
  fs.mkdirSync(HOME, { recursive: true, mode: 0o700 });
  openLog();
  // the UI needs no camera, mic, notifications, geolocation, ...
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  buildMenu();
  createWindow();
  if (await launchBackend()) loadApp();
  else showFailure("the engine did not become healthy.");
});

// Closing the window quits the app (and so stops the engine), on macOS too.
app.on("window-all-closed", () => app.quit());

let stopped = false;
app.on("before-quit", (e) => {
  quitting = true;
  if (stopped) return;
  e.preventDefault();
  stopBackend().finally(() => { stopped = true; logStream?.end(); app.quit(); });
});
// Last resort if Electron is torn down without before-quit (e.g. a signal): never leave the engine behind.
process.on("exit", () => { if (backend) killTree(backend.child); });
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(sig, () => app.quit());
