// Smoke-test the frozen backend the way the app runs it: temp app-data dir, free port, supervised.
// Passes when /api/health answers from the child, the first-run .env is seeded, /api/keys returns no
// secrets, and closing stdin (the app's lifeline) makes it exit 0 within 15 s.
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const desktop = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const exe = path.join(desktop, "backend-dist", "ctb-backend", process.platform === "win32" ? "ctb-backend.exe" : "ctb-backend");
const home = fs.mkdtempSync(path.join(os.tmpdir(), "ctb-smoke-"));
const port = await new Promise((r) => { const s = net.createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });

const env = Object.fromEntries(Object.entries(process.env).filter(([k]) => !/^(CTB_|BYBIT_|KAIROS_|TYPESAFE_)/.test(k)));
const child = spawn(exe, [], {
  env: { ...env, CTB_HOME: home, CTB_PORT: String(port), CTB_SUPERVISED: "1", PYTHONUNBUFFERED: "1",
         CTB_UI_DIR: path.join(desktop, "..", "web-next", "out") },
  stdio: ["pipe", "inherit", "inherit"], windowsHide: true,
});
const exited = new Promise((r) => child.once("exit", (code) => r(code)));
const fail = (msg) => { console.error(`SMOKE FAIL: ${msg}`); child.kill(); process.exit(1); };

const base = `http://127.0.0.1:${port}`;
let health = null;
for (let i = 0; i < 300 && !health; i++) {
  try { health = await (await fetch(`${base}/api/health`)).json(); } catch { await new Promise((r) => setTimeout(r, 300)); }
}
if (!health || health.pid !== child.pid) fail(`no health from the backend (${JSON.stringify(health)})`);
if (!(await fetch(`${base}/`)).ok) fail("UI not served");
const keys = await (await fetch(`${base}/api/keys`)).json();
if (keys.storage !== "app" || keys.kairos.set) fail(`unexpected /api/keys: ${JSON.stringify(keys).slice(0, 200)}`);
if (!fs.readFileSync(path.join(home, ".env"), "utf8").includes("CTB_VENUE=demo")) fail(".env not seeded");
if (!fs.existsSync(path.join(home, "data", "ctb.db"))) fail("database not created in the app-data dir");

child.stdin.end();
const code = await Promise.race([exited, new Promise((r) => setTimeout(() => r("timeout"), 15000))]);
if (code !== 0) fail(`backend did not stop cleanly on stdin close (exit ${code})`);
console.log(`smoke ok: healthy on :${port}, data in ${home}, clean exit`);
fs.rmSync(home, { recursive: true, force: true });
