"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowLeftRight, ChartNoAxesCombined, KeyRound, LayoutGrid, Newspaper, OctagonX, ScrollText, Search, ShieldCheck, SlidersHorizontal, Workflow,
} from "lucide-react";
import { post } from "@/lib/api";
import { loadOverview, selectSymbol, selectTf } from "@/lib/data";
import { getState, setState, useStore, venueOf } from "@/lib/store";
import { age, short } from "@/lib/format";
import { CoinLogo, ModelLogo, Seg, Switch, VenueLogo } from "./ui";

const CITIES: [string, string][] = [["New York", "America/New_York"], ["London", "Europe/London"], ["Dubai", "Asia/Dubai"], ["Mumbai", "Asia/Kolkata"],
  ["Singapore", "Asia/Singapore"], ["Hong Kong", "Asia/Hong_Kong"], ["Tokyo", "Asia/Tokyo"], ["Sydney", "Australia/Sydney"], ["UTC", "UTC"]];

export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

/** The Calibrain Harness mark (same geometry as docs/brand): a flat-capped C and the one calibrated point that closes it. */
function BrandMark() {
  return (
    <svg className="brand-svg" viewBox="0 0 64 64" width="24" height="24" aria-hidden="true">
      <path d="M 46.849 17.151 A 21 21 0 1 0 46.849 46.849" fill="none" stroke="#FFFFFF" strokeWidth="8" strokeLinecap="butt" />
      <rect x="49" y="28" width="8" height="8" fill="#FF8A1F" />
    </svg>
  );
}

const PLACEHOLDER = "Symbol or command (BTC, ASK, EVAL, HELP)";

const VIEWS = [
  ["mon", "F1", "Monitor", LayoutGrid], ["eval", "F2", "Evaluate", ChartNoAxesCombined], ["valid", "F6", "Validation", ShieldCheck],
  ["trades", "F3", "Trades", ArrowLeftRight], ["news", "F4", "News", Newspaper], ["log", "F5", "Log", ScrollText],
] as const;

/** Header: brand, the symbol / command field, the screens, and the switches that change what the bot does. */
export function AppHeader() {
  const symbol = useStore((s) => s.symbol);
  const ov = useStore((s) => s.ov);
  const view = useStore((s) => s.view);
  const hint = useStore((s) => s.cmdHint);
  const input = useRef<HTMLInputElement>(null);

  const flash = (txt: string) => {
    setState({ cmdHint: txt });
    setTimeout(() => setState({ cmdHint: null }), 2500);
  };

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const raw = (input.current?.value || "").trim().toUpperCase();
    if (input.current) input.current.value = "";
    if (!raw) return;
    const syms: string[] = ov?.settings.symbols || [];
    const sym = syms.find((s) => s === raw || s === raw + "USDT");
    const tfm = ({ "1M": "1", "5M": "5", "15M": "15", "1H": "60", "4H": "240" } as Record<string, string>)[raw];
    const pc = raw.match(/^(?:POS|MAXPOS)\s+(\d+)$/);
    const ttf = raw.match(/^(?:TF|TRADE)\s+(1M|5M|15M|1H)$/);
    const views: Record<string, any> = { MON: "mon", EVAL: "eval", VALID: "valid", VAL: "valid", TRD: "trades", TRADES: "trades", NEWS: "news", N: "news", LOG: "log" };
    if (pc) return setPosCap(Number(pc[1]));
    if (ttf) return setState({ modal: { kind: "tf", data: ({ "1M": "1", "5M": "5", "15M": "15", "1H": "60" } as any)[ttf[1]] } });
    if (sym) return selectSymbol(sym);
    if (tfm) return selectTf(tfm);
    if (views[raw]) return setState({ view: views[raw] });
    if (raw === "ASK" || raw === "PRB") return ask(raw === "PRB");
    if (raw === "KEYS") return setState({ modal: { kind: "keys" } });
    if (raw === "STRAT" || raw === "STRATEGY") return setState({ modal: { kind: "strategy" } });
    if (["CONFIG", "CFG", "SET", "SETTINGS", "PREFS", "VENUE"].includes(raw)) return setState({ modal: { kind: "config" } });
    if (raw === "UNIV" || raw === "SYMBOLS" || raw === "SYM") return setState({ modal: { kind: "universe" } });
    if (raw === "HELP" || raw === "?") return setState({ modal: { kind: "help" } });
    flash(`Unknown command "${raw}". Type HELP for the list.`);
  };


  return (
    <header className="app-header">
      <div className="brand" title="Calibrain Harness: forward-testing harness for classification models">
        <BrandMark />
        <span className="brand-name">Calibrain <i>Harness</i></span>
        <span className="brand-tag">beta</span>
      </div>
      <form className="cmd" autoComplete="off" onSubmit={onSubmit} role="search">
        <Search className="search-ic" size={15} strokeWidth={1.6} aria-hidden="true" />
        {symbol && <span className="cmd-sec"><CoinLogo symbol={symbol} size={16} />{short(symbol)}</span>}
        <input id="cmd" ref={input} spellCheck={false} placeholder={hint ?? PLACEHOLDER} aria-label="Symbol or command" />
        <span className="kbd" aria-hidden="true">/</span>
        <button className="go" type="submit">Go</button>
      </form>
      <nav className="nav-tabs" aria-label="Screens">
        {VIEWS.map(([v, k, l, Icon]) => (
          <button key={v} type="button" className={`tab${view === v ? " on" : ""}`} aria-current={view === v ? "page" : undefined}
            onClick={() => setState({ view: v })} data-tip={`${l} (${k})`}>
            <Icon size={15} strokeWidth={1.6} aria-hidden="true" />{l}
          </button>
        ))}
      </nav>
      <div className="cmd-right">
        <button className="iconbtn" type="button" onClick={() => setState({ modal: { kind: "keys" } })} aria-label="API keys" data-tip="API keys">
          <KeyRound size={16} strokeWidth={1.6} />
        </button>
        <button className="btn danger" type="button" onClick={onKill} data-tip="Close bot positions, cancel orders, stop trading">
          <OctagonX size={15} strokeWidth={1.7} aria-hidden="true" />Stop all
        </button>
      </div>
    </header>
  );
}

async function onTrading(on: boolean) {
  const ov = getState().ov;
  if (on && !confirm(`Turn on auto-trade: the bot will place orders on your ${venueOf(ov?.settings).label} account (demo funds) when ${ov?.kairos?.label || "model"} signals pass all gates.`)) return;
  try {
    await post("/api/trading", { enabled: on });
  } catch (err: any) {
    if (/halted/.test(err.message) && confirm(err.message + "\n\nClear the halt and resume?")) await post("/api/trading", { enabled: true, clear_halt: true });
    else alert(err.message);
  }
  loadOverview();
}
async function onExplore(on: boolean) {
  const ov = getState().ov;
  if (on && !confirm(`Explore mode: trade the model's preferred side even when the strict gates say the edge is unproven.\n\nRisk per exploration trade: ${ov?.settings.explore_risk_pct ?? 0.1}% of equity. Safety gates still apply. Orders are only placed while auto-trade is also on.`)) return;
  try { await post("/api/settings", { exploration_mode: on }); } catch (err: any) { alert(err.message); }
  loadOverview();
}
async function onKill() {
  const ov = getState().ov;
  if (confirm(`Stop everything: close all bot positions on ${venueOf(ov?.settings).name}, cancel orders and turn auto-trade off?`)) {
    await post("/api/kill");
    loadOverview();
  }
}

export async function ask(probe: boolean) {
  const sym = getState().symbol;
  if (!sym) return;
  setState({ cmdHint: `Asking the model about ${short(sym)}${probe ? " with probes" : ""}…` });
  try {
    await post("/api/decide", { symbol: sym, probe });
  } catch (e: any) {
    alert(e.message);
  }
  setState({ cmdHint: null });
}

export async function setPosCap(v?: number) {
  const st = getState().ov?.settings;
  if (!st) return;
  let n = v;
  if (n == null) {
    const r = prompt(`Max open positions (1–50). Now ${st.max_positions}.\nOnly affects new entries; open positions stay as they are.\nOne position per symbol, so with ${st.symbols.length} symbols at most ${st.symbols.length} can be open.`, String(st.max_positions));
    if (r == null) return;
    n = Number(r);
  }
  try {
    await post("/api/settings", { max_positions: n });
    await loadOverview();
  } catch (e: any) {
    alert(e.message);
  }
}

/** Second row: what the bot trades on and with (timeframe, model, strategy, venue). */
export function ControlBar() {
  const st = useStore((s) => s.ov?.settings);
  if (!st) return null;
  const v = venueOf(st);
  const explore = !!st.exploration_mode, trading = !!st.trading_enabled;
  const hold = st.horizon_minutes >= 60 ? `${st.horizon_minutes / 60}h` : `${st.horizon_minutes}m`;
  return (
    <div className="ctlbar">
      <span className="ctl" data-tip="How often the model is asked and trades are decided. The chart buttons only change the view.">
        <span className="ctl-l">Trade every</span>
        <Seg label="Trading timeframe" value={st.decision_tf as string} onChange={(tf) => tf !== st.decision_tf && setState({ modal: { kind: "tf", data: tf } })}
          options={Object.entries<any>(st.tf_profiles).map(([tf, p]) => ({ v: tf, label: p.label.toLowerCase(),
            tip: `${p.label}: decide every ${p.label} close, hold up to ${p.horizon_minutes}m, stop from ${p.atr_tf}m ATR, ~${p.required_rph} model requests/h` }))} />
        <span className="ctl-h">hold ≤ {hold}, stop from {st.atr_tf}m ATR</span>
      </span>
      <ModelSwitch />
      <span className="grow" />
      <label className={`toggle-field${explore ? " on-accent" : ""}`} data-tip="Exploration: trade the model's preferred side even when strict quality gates block it, at reduced risk. Safety gates still apply.">
        Explore
        <Switch checked={explore} onChange={onExplore} label="Explore mode" tone="accent" size="sm" />
      </label>
      <label className={`toggle-field${trading ? " on-ok" : ""}`} data-tip={`Allow the bot to place orders on your ${venueOf(st).label} account`}>
        Auto-trade
        <Switch checked={trading} onChange={onTrading} label="Auto-trade" tone="ok" size="sm" />
      </label>

      <button className="btn sm" type="button" onClick={() => setState({ modal: { kind: "strategy" } })}
        data-tip="Brackets, entries, mirror averaging, trade trigger, filters, sizing, drift action">
        <Workflow size={14} strokeWidth={1.6} aria-hidden="true" />Strategy
      </button>
      <button className="btn sm" type="button" onClick={() => setState({ modal: { kind: "config" } })}
        data-tip="Venue (demo / testnet), fees and slippage, risk, signal gates, execution, model budget">
        <SlidersHorizontal size={14} strokeWidth={1.6} aria-hidden="true" />Settings
      </button>
      <button type="button" className="venue-chip" onClick={() => setState({ modal: { kind: "config" } })}
        data-tip={`Orders go to ${v.label}. Signals come from the Bybit mainnet market. Model ${st.model_id}. Click to change the venue.`}>
        <VenueLogo size={20} />Bybit {v.demo ? "Demo" : "Testnet"}
        {v.realism && <span className="pill accent" data-tip="Demo fills and fees are re-booked at the worse of Bybit's numbers and the real mainnet book walk, fee schedule and funding">cost realism</span>}
      </button>
    </div>
  );
}

/** Which classification model makes the decisions. Switching is only allowed while flat and starts a new run. */
function ModelSwitch() {
  const st = useStore((s) => s.ov?.settings);
  if (!st?.models) return null;
  const pick = async (provider: string) => {
    if (provider === st.model_provider) return;
    const m = st.models[provider];
    if (!m.configured) {
      alert(`${m.label} has no API key yet. Add it under Keys first.`);
      setState({ modal: { kind: "keys" } });
      return;
    }
    if (!confirm(`Switch the model to ${m.label} (${m.model_id})?\n\nThe current run ends and a new run starts, so results never mix two models. Calibration for ${m.label} starts from its own history (0.5 prior if none).${m.news_return ? "" : `\n\nNews forecasts (news_return) are Kairos-only and pause while ${m.label} is active.`}`)) return;
    try {
      await post("/api/model", { provider });
    } catch (e: any) {
      alert(e.message);
    }
    loadOverview();
  };
  return (
    <span className="ctl" data-tip="Classification model that answers the questions. Switching starts a new run.">
      <span className="ctl-l">Model</span>
      <Seg label="Model" value={st.model_provider as string} onChange={pick}
        options={Object.entries<any>(st.models).map(([p, m]) => ({ v: p, label: <><ModelLogo provider={p} size={15} />{m.label}</>,
          tip: `${m.label}, ${m.vendor}, ${m.model_id}${m.configured ? "" : ", no key yet"}` }))} />
    </span>
  );
}

export function Alerts() {
  const ov = useStore((s) => s.ov);
  const sock = useStore((s) => s.socket.connected);
  if (!ov) return null;
  const a: React.ReactNode[] = [];
  const keys = (e: React.MouseEvent) => { e.preventDefault(); setState({ modal: { kind: "keys" } }); };
  if (!ov.kairos.configured) a.push(<div key="k" className="alert"><b>{ov.kairos.label || "Model"} key not set.</b> Market data is live; no decisions are made yet.<a href="#" onClick={keys}>Add key</a></div>);
  if (ov.kairos.last_error) a.push(<div key="ke" className="alert crit"><b>{ov.kairos.label || "Model"} error.</b> {ov.kairos.last_error}</div>);
  if (ov.account?.halted_reason) a.push(<div key="h" className="alert crit"><b>Trading halted.</b> {ov.account.halted_reason}</div>);
  if (ov.executor_error) a.push(<div key="x" className="alert crit"><b>Bybit {venueOf(ov.settings).demo ? "Demo" : "Testnet"}.</b> {ov.executor_error}<a href="#" onClick={keys}>Open keys</a></div>);
  if (!sock) a.push(<div key="s" className="alert"><b>Live stream reconnecting.</b> Prices and the account update again as soon as the socket is back.</div>);
  return a.length ? <div className="alerts">{a}</div> : null;
}

export function StatusLine() {
  const ov = useStore((s) => s.ov);
  const sock = useStore((s) => s.socket);
  const symbol = useStore((s) => s.symbol);
  const mon = useStore((s) => s.mon);
  const now = useNow();
  const [fmt] = useState(() => Object.fromEntries(CITIES.map(([, tz]) =>
    [tz, new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" })])));
  const [local, setLocal] = useState("");
  useEffect(() => setLocal(Intl.DateTimeFormat().resolvedOptions().timeZone), []);
  if (!ov) return <footer className="statusline" />;
  const d = ov.data, k = ov.kairos, a = ov.account, st = ov.settings;
  const row = mon?.rows.find((r: any) => r.symbol === symbol);
  const fund = row?.next_funding_ms ? Math.max(0, row.next_funding_ms - now) / 1000 : null;
  const two = (n: number) => String(n | 0).padStart(2, "0");
  const fundTxt = fund === null ? "—" : `${two(fund / 3600)}:${two((fund % 3600) / 60)}:${two(fund % 60)}`;
  const Dot = ({ c }: { c: string }) => <span className={`dot ${c}`} />;
  const stale = sock.lastMsg ? (now - sock.lastMsg) / 1000 : null;
  const world = CITIES.map(([c, tz]) => `${c} ${local ? fmt[tz].format(now) : "--:--"}${tz === local ? " (local)" : ""}`).join("\n");
  return (
    <footer className="statusline">
      <span><Dot c={d.ws_connected ? "ok" : "bad"} />Bybit <b>{d.ws_connected ? "live" : "down"}</b> {Number(d.ws_messages).toLocaleString()} msgs</span>
      <span data-tip="Browser ⇄ harness WebSocket. Latency = server push time to screen.">
        <Dot c={sock.connected && stale !== null && stale < 3 ? "ok" : sock.connected ? "warn" : "bad"} />Stream <b>{sock.connected ? "live" : "down"}</b>
        {sock.latencyMs !== null && <> <b>{sock.latencyMs.toFixed(0)}</b> ms</>}
      </span>
      <span><Dot c={!k.configured ? "warn" : k.last_error ? "bad" : "ok"} />{k.label || "Model"} <b>{k.configured ? (k.last_error ? "error" : "ready") : "no key"}</b> {k.requests_last_hour}/{k.budget_per_hour} req/h</span>
      <span><Dot c={ov.executor_error ? "bad" : a?.private_stream ? "ok" : "warn"} />{venueOf(st).demo ? "Demo" : "Testnet"} <b>{ov.executor_error ? "offline" : a?.private_stream ? "streaming" : "polling"}</b>{a?.last_sync ? ` sync ${age(now / 1000 - a.last_sync)}` : ""}</span>
      {ov.run && <span data-tip={ov.run.name}>Run <b>{ov.run.id}</b></span>}
      <span>Mode <b className={a?.trading_enabled ? "up" : ""}>{a?.trading_enabled ? "auto-trade" : "watching"}</b>{st.exploration_mode && <b style={{ color: "var(--accent)" }}>+ explore</b>}</span>
      <span>Labelled <b>{ov.labelled_total}</b></span>
      <span>{short(symbol || "")} funding in <b>{fundTxt}</b></span>
      <span className="clocks-mini" data-tip={world} suppressHydrationWarning>
        <span>NY <b>{local ? fmt["America/New_York"].format(now) : "--:--"}</b></span>
        <span>LDN <b>{local ? fmt["Europe/London"].format(now) : "--:--"}</b></span>
        <span>UTC <b>{new Date(now).toISOString().slice(11, 19)}</b></span>
      </span>
    </footer>
  );
}
