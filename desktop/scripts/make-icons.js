// Rasterize web-next/app/icon.svg into the desktop icons (run with `npm run build:icons`):
//   build/icon.png (1024, full bleed: Windows/Linux), build/icon.ico (16..256),
//   build/icon.icns (macOS, art inset to Apple's 824/1024 grid; needs macOS `iconutil`).
// The outputs are committed, so CI does not need to run this.
"use strict";
const { app, BrowserWindow } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execFileSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const svg = fs.readFileSync(path.join(root, "..", "web-next", "app", "icon.svg"), "utf8");
const out = path.join(root, "build");

async function render(win, size, inset) {
  const pad = Math.round(size * inset);
  const html = `<html><body style="margin:0;background:transparent;width:${size}px;height:${size}px">
    <img src="data:image/svg+xml;base64,${Buffer.from(svg).toString("base64")}"
         style="position:absolute;left:${pad}px;top:${pad}px;width:${size - 2 * pad}px;height:${size - 2 * pad}px"></body></html>`;
  await win.loadURL("data:text/html;base64," + Buffer.from(html).toString("base64"));
  await new Promise((r) => setTimeout(r, 150));
  const img = await win.webContents.capturePage({ x: 0, y: 0, width: size, height: size });
  return img.resize({ width: size, height: size, quality: "best" }).toPNG();
}

function ico(pngs) { // ICO container with PNG payloads (Vista+)
  const header = Buffer.alloc(6 + 16 * pngs.length);
  header.writeUInt16LE(0, 0); header.writeUInt16LE(1, 2); header.writeUInt16LE(pngs.length, 4);
  let offset = header.length;
  pngs.forEach(([size, buf], i) => {
    const e = 6 + 16 * i;
    header.writeUInt8(size >= 256 ? 0 : size, e); header.writeUInt8(size >= 256 ? 0 : size, e + 1);
    header.writeUInt16LE(1, e + 4); header.writeUInt16LE(32, e + 6);
    header.writeUInt32LE(buf.length, e + 8); header.writeUInt32LE(offset, e + 12);
    offset += buf.length;
  });
  return Buffer.concat([header, ...pngs.map(([, b]) => b)]);
}

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  fs.mkdirSync(out, { recursive: true });
  const win = new BrowserWindow({ width: 1024, height: 1024, show: false, transparent: true, frame: false,
                                  webPreferences: { offscreen: true } });
  fs.writeFileSync(path.join(out, "icon.png"), await render(win, 1024, 0));
  const sizes = [16, 24, 32, 48, 64, 128, 256];
  const pngs = [];
  for (const s of sizes) pngs.push([s, await render(win, s, 0)]);
  fs.writeFileSync(path.join(out, "icon.ico"), ico(pngs));
  if (process.platform === "darwin") {
    const set = fs.mkdtempSync(path.join(os.tmpdir(), "icon-")) + "/icon.iconset";
    fs.mkdirSync(set);
    for (const s of [16, 32, 128, 256, 512]) {
      fs.writeFileSync(path.join(set, `icon_${s}x${s}.png`), await render(win, s, 100 / 1024));
      fs.writeFileSync(path.join(set, `icon_${s}x${s}@2x.png`), await render(win, s * 2, 100 / 1024));
    }
    execFileSync("iconutil", ["-c", "icns", set, "-o", path.join(out, "icon.icns")]);
  }
  console.log("icons written to", out);
  app.quit();
});
