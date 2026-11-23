"use client";
// Shared building blocks for the redesigned terminal: slim icons, coin and venue logos, switches,
// segmented controls and panel headers. Everything else styles through globals.css.
import { useState } from "react";
import { COIN_LOGOS } from "@/lib/coins";
import {
  Activity, ArrowLeftRight, BarChart3, Bell, Bolt, BookOpen, Brain, CandlestickChart, ChartNoAxesCombined, CircleGauge, Clock,
  Cpu, FileJson, Flame, Gauge, Globe, Grid3x3, Hash, Layers, LayoutGrid, LineChart, ListTree, Newspaper, Radar, ScrollText,
  Maximize2, Minimize2, ShieldCheck, Sigma, SlidersHorizontal, Sparkles, Target, Timer, TrendingUp, Wallet, Waves, Workflow, type LucideIcon,
} from "lucide-react";

const PH_ICONS: Record<string, LucideIcon> = {
  MON: LayoutGrid, WEI: Globe, GP: CandlestickChart, KRS: Brain, COMP: LineChart, IMAP: Grid3x3, TOP: Newspaper, BLTR: ScrollText,
  PRTU: Wallet, PST: Layers, DOM: BookOpen, ALRT: Flame, DES: Waves, EVAL: ChartNoAxesCombined, FIND: Sparkles, CAL: Target,
  EDGE: TrendingUp, PIT: BarChart3, SCOR: Sigma, BASE: CircleGauge, PRB: Radar, OPS: Cpu, PNL: Wallet, EXEC: Timer,
  RAW: FileJson, RUNS: ListTree, TRD: ArrowLeftRight, NRET: Newspaper, LOG: ScrollText, VALID: ShieldCheck, SYM: Hash,
  HOUR: Clock, DEC: Brain, STATE: FileJson, HELP: BookOpen, TF: Timer, KEYS: Bolt, UNIV: LayoutGrid, STRAT: Workflow,
  CONFIG: SlidersHorizontal, ALERT: Bell, GAUGE: Gauge, ACT: Activity,
};

/** The icon that stands for a panel (replaces the old three-letter terminal codes). */
export function PhIcon({ code, size = 15 }: { code: string; size?: number }) {
  const I = PH_ICONS[code] || Activity;
  return <I className="ph-icon" size={size} strokeWidth={1.6} aria-hidden="true" />;
}

/** Open one panel in a large view over the page (Esc, the button again, or a click outside closes it). */
export function setExpanded(el: Element | null) {
  const open = !!el && !el.classList.contains("expanded");
  document.querySelectorAll(".pnl.expanded").forEach((x) => x.classList.remove("expanded"));
  if (open) el!.classList.add("expanded");
  document.body.classList.toggle("has-expanded", open);
  window.dispatchEvent(new Event("resize")); // charts and the market map re-measure
}

export function ExpandBtn() {
  return (
    <button type="button" className="expand-btn" aria-label="Expand or collapse this panel" data-tip="Larger view (Esc closes)"
      onClick={(e) => setExpanded(e.currentTarget.closest(".pnl"))}>
      <Maximize2 className="i-max" size={14} strokeWidth={1.7} aria-hidden="true" />
      <Minimize2 className="i-min" size={14} strokeWidth={1.7} aria-hidden="true" />
    </button>
  );
}

/** Panel header: icon, title, an optional right-hand slot, and the expand button (not on sub-headers). */
export function Ph({ code, children, right, sub }: { code: string; children?: React.ReactNode; right?: React.ReactNode; sub?: boolean }) {
  return (
    <div className={`ph${sub ? " sub" : ""}`}>
      <PhIcon code={code} />
      <span className="ph-title">{children}</span>
      {(right !== undefined || !sub) && <span className="ph-r">{right}{!sub && <ExpandBtn />}</span>}
    </div>
  );
}

const MISSING = new Set<string>(); // coins with no logo file: remembered so they never refetch

/** Base coin of a perp symbol: BTCUSDT -> BTC, 1000PEPEUSDT -> PEPE. */
export const baseOf = (sym: string) => sym.replace(/USDT$|USDC$|PERP$/, "").replace(/^1[0]{3,}/, "");

/** Market logo from public/ (scripts/coin-icons.mjs: coins, company logos, metal / ETF / forex badges); a monogram when there is none. */
export function CoinLogo({ symbol, size = 18 }: { symbol: string; size?: number }) {
  const base = baseOf(symbol);
  const file = COIN_LOGOS[base];
  const [failed, setFailed] = useState(MISSING.has(base) || !file);
  if (failed || !base)
    return <span className="coin coin-mono" style={{ width: size, height: size, fontSize: size * 0.42 }} aria-hidden="true">{base.slice(0, 2) || "?"}</span>;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img className="coin" src={`/${file}`} width={size} height={size} alt="" aria-hidden="true" loading="lazy"
      onError={() => { MISSING.add(base); setFailed(true); }} />
  );
}

/** Symbol with its logo, e.g. in tables and headers. */
export function Sym({ symbol, size = 16, full = false }: { symbol: string; size?: number; full?: boolean }) {
  return <span className="symcell"><CoinLogo symbol={symbol} size={size} /><span>{full ? symbol : symbol.replace(/USDT$/, "")}</span></span>;
}

/** Bybit mark; Demo / Testnet are told apart by the label next to it. */
export function VenueLogo({ size = 16 }: { size?: number }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img className="venue-logo" src="/venue-bybit.svg" width={size} height={size} alt="" aria-hidden="true" />;
}

/** On/off switch. A real button with role=switch, so it works with the keyboard and screen readers. */
export function Switch({ checked, onChange, label, tone = "ok", disabled, size = "md" }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; tone?: "ok" | "accent" | "neutral"; disabled?: boolean; size?: "sm" | "md";
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={`switch ${size} tone-${tone}${checked ? " on" : ""}`} onClick={() => onChange(!checked)}>
      <span className="knob" />
    </button>
  );
}

/** Segmented control: one choice out of a few, the active one drawn as an outlined chip. */
export function Seg<T extends string>({ options, value, onChange, label, disabledTip }: {
  options: { v: T; label: React.ReactNode; tip?: string; disabled?: boolean }[]; value: T; onChange: (v: T) => void; label: string; disabledTip?: string;
}) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.v} type="button" className={o.v === value ? "on" : ""} aria-pressed={o.v === value} disabled={o.disabled}
          data-tip={o.disabled ? disabledTip ?? o.tip : o.tip} onClick={() => onChange(o.v)}>{o.label}</button>
      ))}
    </span>
  );
}

/** Logo of the classification model's provider (Kairos is served by Minara Cloud, Jev by TypeSafe). */
export function ModelLogo({ provider, size = 16 }: { provider: string; size?: number }) {
  const p = String(provider || "").toLowerCase();
  if (p !== "kairos" && p !== "jev")
    return <span className="coin coin-mono" style={{ width: size, height: size, fontSize: size * 0.5 }} aria-hidden="true">{p.slice(0, 1).toUpperCase() || "?"}</span>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img className={`model-logo ${p}`} src={`/model-${p}.png`} width={size} height={size} alt="" aria-hidden="true" />;
}
