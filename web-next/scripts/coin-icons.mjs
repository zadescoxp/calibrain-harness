// Logos for every Bybit USDT perpetual, written to public/ and committed (builds stay offline):
//   crypto      web3icons SVG (MIT), else the coin's CoinGecko image (by CoinGecko id when Bybit names one)
//   stock       the company's Simple Icons glyph (CC0) on its brand colour, else a ticker badge
//   ETF, forex, commodity: a drawn badge (gold "Au", silver "Ag", oil, currency, ticker)
// Files are root-level (/coin-<BASE>.svg|png) so every engine version serves them; lib/coins.ts lists them.
// Re-run when Bybit lists new markets:  node scripts/coin-icons.mjs
// Only public endpoints are called (Bybit instrument list, CoinGecko markets + image CDN); nothing about the user is sent.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const pub = path.join(root, "public");
const nm = path.join(root, "node_modules");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const getJSON = async (url, tries = 4) => {
  for (let i = 0; i < tries; i++) {
    const r = await fetch(url, { headers: { "User-Agent": "calibrain-harness-logo-script", Accept: "application/json" } });
    if (r.ok) return r.json();
    await sleep(r.status === 429 ? 20000 : 3000);
  }
  throw new Error(`GET ${url} failed`);
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ------------------------------------------------------------ 1. every Bybit USDT perp, with its type and name
const markets = new Map(); // plain base -> { base, type, name }
const plain = (b) => b.replace(/^1[0]{3,}/, ""); // 1000PEPE -> PEPE
let cursor = "";
do {
  const j = await getJSON(`https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000${cursor ? `&cursor=${cursor}` : ""}`);
  for (const i of j.result.list) {
    if (i.quoteCoin !== "USDT" || i.contractType !== "LinearPerpetual") continue;
    const base = plain(i.baseCoin);
    if (!markets.has(base)) markets.set(base, { base, type: (i.symbolType || "crypto").toLowerCase(), name: i.fullName || "" });
  }
  cursor = j.result.nextPageCursor;
} while (cursor);

// clear the previous run's files
for (const f of fs.readdirSync(pub)) if (/^coin-.+\.(svg|png)$/.test(f)) fs.unlinkSync(path.join(pub, f));
const out = {}; // base -> file name
const write = (base, ext, data) => { fs.writeFileSync(path.join(pub, `coin-${base}.${ext}`), data); out[base] = `coin-${base}.${ext}`; };

// ------------------------------------------------------------ 2. crypto: web3icons first
const w3 = path.join(nm, "@web3icons", "core", "dist", "svgs");
const w3svg = async (kind, name) => {
  const f = path.join(w3, kind, "branded", `${name}.svg.js`);
  if (!fs.existsSync(f)) return null;
  return String((await import(pathToFileURL(f).href)).default).replace(/\s+class="web3icons"/, "");
};
const cryptoLike = (m) => m.type === "crypto" || m.type === "innovation";
for (const m of markets.values()) {
  if (!cryptoLike(m)) continue;
  const svg = await w3svg("tokens", m.base);
  if (svg) write(m.base, "svg", svg);
}

// ------------------------------------------------------------ 3. crypto: CoinGecko for the rest
const need = [...markets.values()].filter((m) => cryptoLike(m) && !out[m.base]);
const bySymbol = new Map(); // lower symbol -> image of the highest market cap coin with it
const byId = new Map();
for (let page = 1; page <= 8; page++) {
  const list = await getJSON(`https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=${page}`);
  for (const c of list) {
    if (!c.image) continue;
    byId.set(c.id, c.image);
    if (!bySymbol.has(c.symbol)) bySymbol.set(c.symbol, c.image);
  }
  if (list.length < 250) break;
  await sleep(2500);
}
// innovation-zone names are CoinGecko ids ("acurast"); look up the ones not in the top 2000
const ids = need.map((m) => m.name).filter((n) => /^[a-z0-9-]+$/.test(n) && !byId.has(n));
for (let i = 0; i < ids.length; i += 200) {
  const list = await getJSON(`https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=${ids.slice(i, i + 200).join(",")}&per_page=250`);
  for (const c of list) if (c.image) byId.set(c.id, c.image);
  await sleep(2500);
}
const queue = need.map((m) => [m.base, byId.get(m.name) || bySymbol.get(m.base.toLowerCase())]).filter(([, u]) => u);
let gecko = 0;
for (let i = 0; i < queue.length; i += 8) {
  await Promise.all(queue.slice(i, i + 8).map(async ([base, url]) => {
    try {
      const r = await fetch(url.replace("/large/", "/small/"));
      if (!r.ok) return;
      const buf = Buffer.from(await r.arrayBuffer());
      if (buf.length > 100 && buf.length < 200_000) { write(base, "png", buf); gecko++; }
    } catch { /* leave it to the badge */ }
  }));
}

// ------------------------------------------------------------ 4. stocks: Simple Icons by company name
const si = JSON.parse(fs.readFileSync(path.join(nm, "simple-icons", "data", "simple-icons.json"), "utf8"));
const norm = (s) => s.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9 ]/g, " ")
  .replace(/\b(inc|corp|corporation|co|company|plc|ltd|limited|holdings?|group|nv|sa|ag|se|derivatives|class [ab]|platforms?|technolog(y|ies)|the)\b/g, " ")
  .replace(/\s+/g, " ").trim();
const siByName = new Map();
for (const ic of si) {
  siByName.set(norm(ic.title), ic);
  for (const a of ic.aliases?.aka || []) if (!siByName.has(norm(a))) siByName.set(norm(a), ic);
}
const brandSvg = (ic) => {
  const p = fs.readFileSync(path.join(nm, "simple-icons", "icons", `${ic.slug}.svg`), "utf8").match(/<path d="([^"]+)"/)?.[1];
  if (!p) return null;
  const hex = `#${ic.hex}`;
  const light = parseInt(ic.hex.slice(0, 2), 16) * 0.299 + parseInt(ic.hex.slice(2, 4), 16) * 0.587 + parseInt(ic.hex.slice(4, 6), 16) * 0.114 > 160;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" fill="${hex}"/><g transform="translate(5 5) scale(.5833)"><path fill="${light ? "#111" : "#fff"}" d="${p}"/></g></svg>`;
};

// ------------------------------------------------------------ 5. badges for everything else
const badge = (text, bg, fg = "#fff", ring = "") =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" fill="${bg}"/>${ring}` +
  `<text x="12" y="${text.length > 3 ? 15 : 16}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-weight="700" font-size="${text.length > 3 ? 6.6 : text.length > 2 ? 8 : 10}" fill="${fg}">${esc(text)}</text></svg>`;
const COMMODITY = {
  XAU: badge("Au", "#b8902c", "#fff7d6", '<circle cx="12" cy="12" r="9" fill="none" stroke="#f3d27a" stroke-width="1.2"/>'),
  XAUT: badge("Au", "#b8902c", "#fff7d6", '<circle cx="12" cy="12" r="9" fill="none" stroke="#f3d27a" stroke-width="1.2"/>'),
  PAXG: badge("Au", "#b8902c", "#fff7d6", '<circle cx="12" cy="12" r="9" fill="none" stroke="#f3d27a" stroke-width="1.2"/>'),
  XAG: badge("Ag", "#8e98a6", "#ffffff", '<circle cx="12" cy="12" r="9" fill="none" stroke="#dfe5ec" stroke-width="1.2"/>'),
  XPT: badge("Pt", "#6f7c8c"), XPD: badge("Pd", "#6f7c8c"), COPPER: badge("Cu", "#b06a3b"), HG: badge("Cu", "#b06a3b"),
  CL: `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect width="24" height="24" rx="6" fill="#1c1d21"/><path d="M12 4.5c2.6 3.6 4.6 6.3 4.6 8.8a4.6 4.6 0 0 1-9.2 0c0-2.5 2-5.2 4.6-8.8z" fill="#e6e8eb"/></svg>`,
  NG: badge("Gas", "#2b5aa8"),
};
let stocks = 0, badges = 0;
for (const m of markets.values()) {
  if (out[m.base]) continue;
  if (COMMODITY[m.base]) { write(m.base, "svg", COMMODITY[m.base]); badges++; continue; }
  if (m.type === "stock") {
    const n = norm(m.name);
    const ic = siByName.get(n) || siByName.get(n.split(" ")[0]) || siByName.get(norm(m.base.replace(/STOCK$/, "")));
    const svg = ic && brandSvg(ic);
    if (svg) { write(m.base, "svg", svg); stocks++; continue; }
    write(m.base, "svg", badge(m.base.replace(/STOCK$/, "").slice(0, 4), "#2a3a5c", "#dbe6ff")); badges++; continue;
  }
  if (m.type === "etf") { write(m.base, "svg", badge(m.base.replace(/STOCK$/, "").slice(0, 4), "#2d4a3e", "#d6f5e6")); badges++; continue; }
  if (m.type === "forex") { write(m.base, "svg", badge(m.base.slice(0, 3), "#3b3f6b", "#e3e6ff")); badges++; continue; }
  if (m.type === "commodity") { write(m.base, "svg", badge(m.base.slice(0, 3), "#5b4a2a", "#fbe8c3")); badges++; continue; }
}

// ------------------------------------------------------------ 6. venue logo + the index the UI reads
fs.writeFileSync(path.join(pub, "venue-bybit.svg"), await w3svg("exchanges", "bybit"));
const sorted = Object.fromEntries(Object.entries(out).sort(([a], [b]) => a.localeCompare(b)));
fs.writeFileSync(path.join(root, "lib", "coins.ts"),
  "// Generated by scripts/coin-icons.mjs: market base -> logo file in public/ (a missing base gets a monogram, no request).\n" +
  `export const COIN_LOGOS: Record<string, string> = ${JSON.stringify(sorted)};\n`);
const missing = [...markets.keys()].filter((b) => !out[b]);
console.log(`${markets.size} markets: ${Object.keys(out).length} logos (${gecko} from CoinGecko, ${stocks} company logos, ${badges} badges), ` +
  `${missing.length} monograms: ${missing.slice(0, 40).join(" ")}`);
