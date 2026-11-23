// Chart indicators: the catalogue the Indicators menu offers, which ones are on (kept per browser), and the maths.
// Every function takes the candles oldest-first and returns one value per candle (null while warming up).
import { useSyncExternalStore } from "react";

export type Candle = { time: number; open: number; high: number; low: number; close: number; volume: number };
export type Pt = number | null;

export type IndicatorId = "vol" | "ema20" | "ema50" | "ema200" | "sma50" | "bb" | "vwap" | "rsi" | "macd" | "signals" | "flat";
export const INDICATORS: { id: IndicatorId; label: string; group: "Overlays" | "Below the chart" | "Model"; help: string }[] = [
  { id: "vol", label: "Volume", group: "Overlays", help: "Traded volume per candle" },
  { id: "ema20", label: "EMA 20", group: "Overlays", help: "Exponential moving average, 20 candles" },
  { id: "ema50", label: "EMA 50", group: "Overlays", help: "Exponential moving average, 50 candles" },
  { id: "ema200", label: "EMA 200", group: "Overlays", help: "Exponential moving average, 200 candles" },
  { id: "sma50", label: "SMA 50", group: "Overlays", help: "Simple moving average, 50 candles" },
  { id: "bb", label: "Bollinger Bands", group: "Overlays", help: "20-candle SMA ± 2 standard deviations" },
  { id: "vwap", label: "VWAP", group: "Overlays", help: "Volume-weighted average price, resets at 00:00 UTC" },
  { id: "rsi", label: "RSI 14", group: "Below the chart", help: "Relative strength index (Wilder, 14); 70 / 30 marked" },
  { id: "macd", label: "MACD 12 26 9", group: "Below the chart", help: "MACD line, signal line and histogram" },
  { id: "signals", label: "Model signals", group: "Model", help: "Long and short decisions, drawn at the price they were made" },
  { id: "flat", label: "Flat decisions too", group: "Model", help: "Also mark the bars where the model said no trade" },
];

// ------------------------------------------------------------ which are on (a tiny store; remembered per browser)
const KEY = "ch.indicators.v1";
const DEFAULT: IndicatorId[] = ["signals"];
let on: IndicatorId[] = DEFAULT;
let loaded = false;
const subs = new Set<() => void>();
function load() {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) on = (JSON.parse(raw) as IndicatorId[]).filter((x) => INDICATORS.some((i) => i.id === x));
  } catch { /* private window: defaults */ }
}
export function useIndicators(): IndicatorId[] {
  return useSyncExternalStore((l) => { subs.add(l); return () => subs.delete(l); }, () => { load(); return on; }, () => DEFAULT);
}
export function setIndicator(id: IndicatorId, enabled: boolean) {
  load();
  on = enabled ? (on.includes(id) ? on : [...on, id]) : on.filter((x) => x !== id);
  try { localStorage.setItem(KEY, JSON.stringify(on)); } catch { /* not persisted */ }
  subs.forEach((l) => l());
}

// ------------------------------------------------------------ maths
export function sma(v: number[], p: number): Pt[] {
  const out: Pt[] = [];
  let s = 0;
  v.forEach((x, i) => { s += x; if (i >= p) s -= v[i - p]; out.push(i >= p - 1 ? s / p : null); });
  return out;
}
export function ema(v: number[], p: number): Pt[] {
  const k = 2 / (p + 1);
  let e: number | null = null;
  return v.map((x, i) => { e = e === null ? x : x * k + e * (1 - k); return i >= p - 1 ? e : null; });
}
export function bollinger(v: number[], p = 20, mult = 2) {
  const mid = sma(v, p);
  const up: Pt[] = [], lo: Pt[] = [];
  v.forEach((_, i) => {
    const m = mid[i];
    if (m === null) { up.push(null); lo.push(null); return; }
    let ss = 0;
    for (let j = i - p + 1; j <= i; j++) ss += (v[j] - m) ** 2;
    const sd = Math.sqrt(ss / p);
    up.push(m + mult * sd); lo.push(m - mult * sd);
  });
  return { mid, up, lo };
}
export function vwap(c: Candle[]): Pt[] {
  let day = -1, pv = 0, vol = 0;
  return c.map((b) => {
    const d = Math.floor(b.time / 86400);
    if (d !== day) { day = d; pv = 0; vol = 0; }
    const tp = (b.high + b.low + b.close) / 3;
    pv += tp * b.volume; vol += b.volume;
    return vol > 0 ? pv / vol : null;
  });
}
export function rsi(v: number[], p = 14): Pt[] {
  const out: Pt[] = [null];
  let g = 0, l = 0;
  for (let i = 1; i < v.length; i++) {
    const d = v[i] - v[i - 1], up = Math.max(d, 0), dn = Math.max(-d, 0);
    if (i <= p) { g += up / p; l += dn / p; out.push(i === p ? (l === 0 ? 100 : 100 - 100 / (1 + g / l)) : null); continue; }
    g = (g * (p - 1) + up) / p; l = (l * (p - 1) + dn) / p;
    out.push(l === 0 ? 100 : 100 - 100 / (1 + g / l));
  }
  return out;
}
export function macd(v: number[], f = 12, s = 26, sig = 9) {
  const ef = ema(v, f), es = ema(v, s);
  const line: Pt[] = v.map((_, i) => (ef[i] !== null && es[i] !== null ? (ef[i] as number) - (es[i] as number) : null));
  const start = line.findIndex((x) => x !== null);
  const sigLine: Pt[] = line.map(() => null);
  if (start >= 0) {
    const tail = ema(line.slice(start) as number[], sig);
    tail.forEach((x, j) => (sigLine[start + j] = x));
  }
  const hist: Pt[] = line.map((x, i) => (x !== null && sigLine[i] !== null ? x - (sigLine[i] as number) : null));
  return { line, signal: sigLine, hist };
}
