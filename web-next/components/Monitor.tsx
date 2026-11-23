"use client";
import { Fragment, memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import { loadOverview, selectSymbol, selectTf } from "@/lib/data";
import { setState, useModelLabel, useStore, venueOf } from "@/lib/store";
import { age, cssVar, dt, fx, hhmm, hhmmss, isNum, px, sg, short, TFL, ud, usd } from "@/lib/format";
import { IndicatorsMenu, PerfChart, PerfLegend, PriceChart } from "./Charts";
import { ask, setPosCap, useNow } from "./TopBar";
import { baseOf, CoinLogo, ExpandBtn, ModelLogo, Ph, PhIcon, Seg, Sym } from "./ui";
import { COIN_LOGOS } from "@/lib/coins";
import { CircleCheck, CircleSlash, Minus, Radar, TrendingDown, TrendingUp } from "lucide-react";


export function MonitorView({ hidden }: { hidden: boolean }) {
  return (
    <main className="view on" style={hidden ? { display: "none" } : undefined}>
      <div className="grid-mon">
        <section className="pnl a-mon">
          <Ph code="MON" right={<><span className="live"><span className="pulse" />live</span><button className="mini" onClick={() => setState({ modal: { kind: "universe" } })} data-tip="Choose which perps the bot watches and trades (UNIV)">Edit list</button></>}>Markets</Ph>
          <div className="pb tight scroll watch-scroll"><Watchlist /></div>
          <Ph code="WEI" sub right="Yahoo, alternative.me">Cross-asset</Ph>
          <div className="pb tight scroll macro"><Macro /></div>
        </section>
        <section className="pnl a-chart">
          <ChartHeader />
          <PriceChart />
        </section>
        <section className="pnl a-kai">
          <Ph code="KRS" right={<><button className="mini" onClick={() => ask(false)} data-tip="Ask the model now (1 request)">Ask now</button><button className="mini" onClick={() => ask(true)} aria-label="Ask with consistency probes" data-tip="Ask + repeat and mirror consistency probes (3 extra requests)"><Radar size={13} strokeWidth={1.7} /></button></>}><ModelBadge /> signal</Ph>
          <div className="pb scroll"><KairosPanel /></div>
        </section>
        <section className="pnl a-acct"><Account /></section>
        <section className="pnl a-perf">
          <Ph code="COMP" right={<PerfLegend />}>Relative performance, 24h</Ph>
          <PerfChart />
        </section>
        <section className="pnl a-map">
          <MarketMap visible={!hidden} />
        </section>
        <section className="pnl a-book"><BookPanel /></section>
        <section className="pnl a-news">
          <Ph code="TOP" right="CoinDesk, Cointelegraph, Decrypt">News</Ph>
          <div className="pb tight scroll"><NewsMini /></div>
        </section>
        <section className="pnl a-tape"><Tape /></section>
        <section className="pnl a-stats"><Stats /></section>
        <section className="pnl a-log">
          <Ph code="BLTR" right="click a row for the full request and response">Decisions, <ModelName /></Ph>
          <div className="pb tight scroll"><Blotter /></div>
        </section>
      </div>
    </main>
  );
}

/* ---------------------------------------------------------------- watchlist */
function spark(vals: number[] | undefined, w = 54, h = 16) {
  if (!vals || vals.length < 2) return null;
  const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
  const d = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 1 - ((v - mn) / r) * (h - 2)).toFixed(1)}`).join(" ");
  return <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`}><polyline points={d} fill="none" stroke={vals[vals.length - 1] >= vals[0] ? "var(--up)" : "var(--down)"} strokeWidth={1.25} /></svg>;
}

const LastCell = memo(function LastCell({ value }: { value?: string }) {
  const prev = useRef(value);
  const ref = useRef<HTMLTableCellElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && prev.current && value && prev.current !== value) {
      el.classList.remove("flash-up", "flash-dn");
      void el.offsetWidth; // restart the animation
      el.classList.add(Number(value) > Number(prev.current) ? "flash-up" : "flash-dn");
    }
    prev.current = value;
  }, [value]);
  return <td ref={ref} className="r">{px(value)}</td>;
});

function Watchlist() {
  const mon = useStore((s) => s.mon);
  const ticks = useStore((s) => s.ticks);
  const symbol = useStore((s) => s.symbol);
  const latest = useStore((s) => s.latest);
  if (!mon) return <div className="empty">Loading…</div>;
  return (
    <table className="t">
      <thead><tr><th>Market</th><th className="r">Last</th><th className="r">24h</th><th className="wl-trend">Trend</th><th className="r wl-fund">Funding</th><th className="r">Signal</th></tr></thead>
      <tbody>
        {mon.rows.map((r: any) => {
          const t = ticks[r.symbol];
          const last = t?.last ?? r.last, pct = t?.pct24h ?? r.pct24h;
          const d = latest[r.symbol];
          const tip = `${r.symbol}\nfunding ${sg(isNum(r.funding) ? r.funding * 100 : null, 4, "%")}\nmark ${px(t?.mark ?? r.mark)} · index ${px(r.index)}\n24h H ${px(r.high24h)} L ${px(r.low24h)}\nturnover $${usd(r.turnover24h)} · OI $${usd(r.oi_value)}\ntestnet ${px(t?.testnet_last ?? r.testnet_last)} (${sg(r.divergence_bp, 1)} bp)`;
          return (
            <tr key={r.symbol} className={`click ${r.symbol === symbol ? "sel" : ""}`} onClick={() => selectSymbol(r.symbol)} data-tip={tip}>
              <td className="sym"><Sym symbol={r.symbol} /></td>
              <LastCell value={last} />
              <td className={`r ${ud(pct)}`}>{sg(pct, 2, "%")}</td>
              <td className="wl-trend">{spark(r.spark)}</td>
              <td className={`r wl-fund ${ud(r.funding)}`}>{sg(isNum(r.funding) ? r.funding * 100 : null, 4)}</td>
              <td className="r">
                {!d ? <span className="tag flat">—</span> : d.error_kind ? <span className="tag err" data-tip={d.error_kind}>error</span> : (
                  <span className={`tag ${d.action}`} data-tip={`${d.action}${d.exploration ? " (explore)" : ""}, ${age(Date.now() / 1000 - d.ts)} ago\nEV long ${sg(d.ev_long_r, 3)}R, short ${sg(d.ev_short_r, 3)}R`}>{d.action || "—"}</span>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Macro() {
  const mon = useStore((s) => s.mon);
  const names: Record<string, string> = { SPX_fut: "S&P 500 futures", NDX_fut: "Nasdaq 100 futures", DXY: "Dollar index", Gold: "Gold futures", US10Y: "US 10Y yield", VIX: "VIX" };
  const fg = mon?.fear_greed;
  return (
    <table className="t">
      <thead><tr><th>Instrument</th><th className="r">Last</th><th className="r">8h</th></tr></thead>
      <tbody>
        {Object.entries<any>(mon?.macro || {}).map(([k, v]) => (
          <tr key={k}><td className="amb">{names[k] || k}</td><td className="r">{fx(v.last, k === "US10Y" ? 3 : 2)}</td><td className={`r ${ud(v.chg_8h_pct)}`}>{sg(v.chg_8h_pct, 2, "%")}</td></tr>
        ))}
        {fg && <tr data-tip={`Crypto fear & greed: ${fg.value} (${fg.label}); a week ago ${fg.week_ago}`}><td className="amb">Fear &amp; greed</td><td className="r">{fg.value}</td><td className="r muted">{String(fg.label).toLowerCase()}</td></tr>}
      </tbody>
    </table>
  );
}

/* ---------------------------------------------------------------- chart header */
function ChartHeader() {
  const mkt = useStore((s) => s.mkt);
  const symbol = useStore((s) => s.symbol);
  const tf = useStore((s) => s.tf);
  const tick = useStore((s) => (symbol ? s.ticks[symbol] : undefined));
  const maxDiv = useStore((s) => s.ov?.settings.max_venue_divergence_bp ?? 150);
  const last = tick?.last ?? mkt?.ticker?.lastPrice;
  const pct = tick?.pct24h ?? (isNum(mkt?.ticker?.price24hPcnt) ? 100 * mkt.ticker.price24hPcnt : null);
  const tn = tick?.testnet_last ?? mkt?.testnet_ticker?.lastPrice;
  const div = tn && last ? 10000 * (Number(tn) / Number(last) - 1) : null;
  const venueName = useStore((s) => venueOf(s.ov?.settings).demo ? "Demo" : "Testnet");
  return (
    <div className="ph chart-head">
      {symbol && <CoinLogo symbol={symbol} size={26} />}
      <span className="ph-title" style={{ flexDirection: "column", alignItems: "flex-start", gap: 0 }}>
        <span style={{ display: "inline-flex", gap: 10, alignItems: "baseline" }}>
          <span className="px">{px(last)}</span><span className={ud(pct)}>{sg(pct, 2, "%")}</span>
        </span>
        <span className="muted" style={{ fontSize: 11.5 }}>{symbol} perpetual, Bybit mainnet price</span>
      </span>
      <span className="seg" role="group" aria-label="Chart timeframe">
        {Object.entries(TFL).map(([k, l]) => <button key={k} type="button" className={k === tf ? "on" : ""} aria-pressed={k === tf} onClick={() => selectTf(k)}>{l.toLowerCase()}</button>)}
      </span>
      <IndicatorsMenu />
      <span className="ph-r">
        {tn && <span data-tip="Order venue last price vs mainnet last. Entries and brackets use the venue book.">{venueName} {px(tn)} <span className={div !== null && Math.abs(div) > maxDiv ? "dn" : ""}>{sg(div, 1)} bp</span></span>}
        <ExpandBtn />
      </span>
    </div>
  );
}

/* ---------------------------------------------------------------- model signal */
function ModelName() {
  return <>{useModelLabel()}</>;
}
function ModelBadge() {
  const p = useStore((s) => s.ov?.settings.model_provider) || "kairos";
  return <><ModelLogo provider={p} size={18} /><ModelName /></>;
}

function PBars({ probs, labels }: { probs: Record<string, number>; labels?: Record<string, string> }) {
  const e = Object.entries(probs), mx = Math.max(...e.map((x) => x[1]));
  return <>{e.map(([k, v]) => (
    <div className="pr" key={k}><span className="lb" title={labels?.[k] || k}>{labels?.[k] || k}</span><div className="tr"><div className={`fl ${v === mx ? "top" : ""}`} style={{ width: `${(100 * v).toFixed(1)}%` }} /></div><span className="vv">{(100 * v).toFixed(0)}%</span></div>
  ))}</>;
}

function KairosPanel() {
  const symbol = useStore((s) => s.symbol);
  const d = useStore((s) => (s.symbol ? s.latest[s.symbol] : undefined));
  const mkt = useStore((s) => s.mkt);
  const ov = useStore((s) => s.ov);
  useNow(5000);
  const pv = mkt?.symbol === symbol ? mkt?.state_preview : null;
  if (!d)
    return (
      <>
        <div className="sig-hero">
          <span className="sig-act flat"><Minus size={20} strokeWidth={1.8} aria-hidden="true" />Waiting</span>
          <div className="sig-meta"><b>No signal yet</b><span className="muted">{ov?.kairos.configured ? `${ov.kairos.label} is asked on every closed ${ov.settings.decision_tf_minutes}m bar. Ask now asks straight away.` : `Add a ${ov?.kairos.label || "model"} key in Keys to start receiving signals.`}</span></div>
        </div>
        {!ov?.kairos.configured && <button className="btn primary" style={{ marginBottom: 12 }} onClick={() => setState({ modal: { kind: "keys" } })}>Add {ov?.kairos.label || "model"} key</button>}
        {pv && <dl className="facts">
          <div><dt>Next stop</dt><dd>{fx(pv.meta.sl_bp / 100, 2)}%</dd></div><div><dt>Next target</dt><dd>{fx(pv.meta.tp_bp / 100, 2)}%</dd></div><div><dt>Horizon</dt><dd>{pv.meta.horizon_min} min</dd></div>
        </dl>}
      </>
    );
  if (d.error_kind) return <><div className="sig-hero"><span className="sig-act dn"><CircleSlash size={20} strokeWidth={1.8} aria-hidden="true" />Error</span><div className="sig-meta"><b>{d.error_kind}</b><span className="muted">{dt(d.ts)}, recorded as a request failure, never as a &quot;no trade&quot; judgment.</span></div></div><p className="muted" style={{ margin: 0 }}>{d.error_msg}</p></>;
  const p = d.parsed || {}, sl = d.sl_bp / 100, tp = d.tp_bp / 100;
  const e = [-2, -1, -1 / 3, 1 / 3, 1, 2].map((x) => x * sl);
  const binL: Record<string, string> = {};
  ["< " + fx(e[0], 2), `${fx(e[0], 2)}…${fx(e[1], 2)}`, `${fx(e[1], 2)}…${fx(e[2], 2)}`, `${fx(e[2], 2)}…${fx(e[3], 2)}`, `${fx(e[3], 2)}…${fx(e[4], 2)}`, `${fx(e[4], 2)}…${fx(e[5], 2)}`, "> " + fx(e[5], 2)]
    .forEach((l, i) => (binL["bin_" + i] = l + "%"));
  const top = (q: string) => (p[q] ? Object.entries<number>(p[q].probs).sort((a, b) => b[1] - a[1])[0] : null);
  const reg = top("regime"), crowd = top("crowding");
  const v2 = d.p_up !== undefined && d.p_up !== null;
  const reasons = String(d.reason || "").split(/;\s*/).map((r) => r.trim()).filter(Boolean);
  const act = d.action === "long" ? "up" : d.action === "short" ? "dn" : "flat";
  const ActIcon = d.action === "long" ? TrendingUp : d.action === "short" ? TrendingDown : Minus;
  const ev = (k: "long" | "short") => (
    <div className="ev-tile" data-tip={v2 ? `Calibrated EV (the model blended with this bracket's base rates).\nRaw model ${sg(d[`ev_${k}_raw_r`], 3)}R, base rates alone ${sg(d[`ev_${k}_base_r`], 3)}R` : undefined}>
      <span className="l">EV {k}{v2 ? <span className="muted"> calibrated</span> : null}</span>
      <span className={`n ${ud(d[`ev_${k}_r`])}`}>{sg(d[`ev_${k}_r`], 2)}R</span>
      {v2 && isNum(d[`ev_${k}_raw_r`]) && <span className="s">raw {sg(d[`ev_${k}_raw_r`], 2)}R</span>}
    </div>
  );
  const pct = (x: any) => (isNum(x) ? `${(100 * Number(x)).toFixed(0)}%` : "—");
  const facts: [string, React.ReactNode, string?][] = v2 ? [
    ["P(up), calibrated", <>{pct(d.p_up)} <span className="muted">raw {pct(d.p_up_raw)}</span></>, "Calibrated P(price higher at horizon); raw = model alone"],
    ["Big move", <>{pct(d.p_big_move)} <span className="muted">base {pct(d.p_big_move_base)}</span></>, "Calibrated P(move ≥ 1 stop either way) against its base rate"],
    ["Mean reversion", p.mean_reversion ? pct(p.mean_reversion.p) : "—"],
    ["Model weight", fx(typeof d.weights?.long_bracket === "object" ? d.weights.long_bracket.model : d.weights?.long_bracket, 2), "w in w × model + (1 − w) × base rates"],
    ...(d.events !== undefined ? [
      ["Events", d.events?.length ? d.events.join(", ").replace(/_/g, " ") : "none", "Market events on the decision bar. With trade trigger = events, trades happen only on these bars."],
      ["Regime", d.regime ? String(d.regime).replace(/_/g, " ") : "—", "GARCH volatility state × trend (efficiency ratio)"],
      ["Order flow", <>{sg(d.flow_score, 2)} <span className="muted">side from {d.side_source || "model"}{d.symmetrized ? ", mirrored" : ""}</span></>, "Order-flow direction score in [−1, 1]"],
    ] as [string, React.ReactNode, string?][] : []),
    ...(d.costs ? [
      ["Costs t / s / time", `${fx(d.costs.target_bp, 1)} / ${fx(d.costs.stop_bp, 1)} / ${fx(d.costs.time_bp, 1)} bp`, "Cost charged to each outcome: target (maker exit), stop (taker + slippage), time exit"],
      ["Bracket", d.bracket?.mode === "vol" ? `${fx(d.bracket.sigma_horizon_bp, 0)} bp σ, ${d.bracket.cost_floor_bound ? "cost floor" : "vol"}` : "ATR", "How the stop was sized"],
    ] as [string, React.ReactNode, string?][] : []),
  ] : [
    ["Regime", reg ? `${reg[0].replace("_", " ")} ${(100 * reg[1]).toFixed(0)}%` : "—"],
    ["Crowding", crowd ? `${crowd[0].replace("_", " ")} ${(100 * crowd[1]).toFixed(0)}%` : "—"],
    ["Mean reversion", p.mean_reversion ? pct(p.mean_reversion.p) : "—"],
    ["Data sufficient", p.data_sufficient ? pct(p.data_sufficient.p) : "—"],
  ];
  facts.push(["Asked", `${hhmmss(d.ts)}, ${age(Date.now() / 1000 - d.ts)} ago`], ["Latency", `${fx(d.latency_ms, 0)} ms`],
    ["Coherence", <>{fx(d.coherence, 2)} <span className={d.issues?.length ? "dn" : "muted"}>{d.issues?.length || 0} issues</span></>]);
  return (
    <>
      <div className="sig-hero">
        <span className={`sig-act ${act}`}><ActIcon size={20} strokeWidth={1.8} aria-hidden="true" />{d.action ? d.action[0].toUpperCase() + d.action.slice(1) : "—"}</span>
        <div className="sig-meta">
          <b>{d.action === "flat" ? "No trade on this bar" : `${d.action === "long" ? "Long" : "Short"} signal`}{d.exploration && <span className="tag xp" style={{ marginLeft: 8 }} data-tip="Exploration trade: strict gates would have stayed flat">explore</span>}</b>
          <span className="muted">setup {fx(d.setup_quality, 2)} of 4, asked {age(Date.now() / 1000 - d.ts)} ago</span>
        </div>
      </div>
      {reasons.length > 0 && (
        <ul className="sig-why" aria-label="Why">
          {reasons.map((r, i) => (
            <li key={i}>{d.action === "flat" ? <CircleSlash size={13} strokeWidth={1.8} aria-hidden="true" /> : <CircleCheck size={13} strokeWidth={1.8} aria-hidden="true" />}<span>{r}</span></li>
          ))}
        </ul>
      )}
      <div className="ev-row">{ev("long")}{ev("short")}</div>
      {(["long_bracket", "short_bracket"] as const).map((q) => p[q] && (
        <div className="qb" key={q}>
          <div className="qt"><span>{q === "long_bracket" ? "Long" : "Short"} bracket</span><span>first touch ≤ {d.horizon_min ?? ov?.settings.horizon_minutes}m</span></div>
          <PBars probs={p[q].probs} labels={{ target_first: `target ${q === "long_bracket" ? "+" : "−"}${fx(tp, 2)}%`, stop_first: `stop ${q === "long_bracket" ? "−" : "+"}${fx(sl, 2)}%`, neither: "neither (time)" }} />
        </div>
      ))}
      {p.return_distribution && <div className="qb"><div className="qt"><span>Return distribution</span><span>E = {sg(d.expected_move_in_d, 2)} × stop</span></div><PBars probs={p.return_distribution.probs} labels={binL} /></div>}
      <dl className="facts">
        {facts.map(([k, v, tip]) => <div key={k} data-tip={tip}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    </>
  );
}

/* ---------------------------------------------------------------- account */
function Account() {
  const ov = useStore((s) => s.ov);
  const now = useNow();
  const a = ov?.account, w = a?.wallet || {};
  const closePos = async (sym: string) => {
    if (confirm(`Market-close ${sym} on ${venueOf(ov?.settings).name} (reduce-only)?`)) {
      await post("/api/close", { symbol: sym });
      loadOverview();
    }
  };
  const vn = venueOf(ov?.settings);
  const title = <span data-tip={vn.realism ? "Equity is shown as Bybit reports it; trade P&L is re-booked at real-market cost" : undefined}>{vn.demo ? "Demo" : "Testnet"} account</span>;
  if (!a) return <><Ph code="PRTU">{title}</Ph><div className="empty">{ov?.executor_error || `No Bybit ${vn.name} keys yet.`} <button className="mini" onClick={() => setState({ modal: { kind: "keys" } })}>Add keys</button></div></>;
  const st = ov.settings;
  const cells: [string, React.ReactNode, string][] = [
    ["Equity", fx(w.equity), "USDT, unified account"], ["Available", fx(w.available), "USDT"],
    ["Unrealised", <span key="u" className={ud(w.unrealized_pnl)}>{sg(w.unrealized_pnl)}</span>, "USDT"],
    ["Today P&L", <span key="d" className={ud(a.daily_pnl)}>{sg(a.daily_pnl)}</span>,
      a.cost_realism ? `booked, realism −${fx(a.realism_adj_today ?? 0)}, halts at −${st.daily_loss_limit_pct}%` : `halts at −${st.daily_loss_limit_pct}%`],
    ["Positions", <span key="p">{a.positions.length}/{st.max_positions} <button className="mini" onClick={() => setPosCap()} data-tip="Change the max number of open positions. Applies to new entries only.">Cap</button></span>, `${a.open_orders} open orders`],
    ["Risk per trade", `${st.exploration_mode ? Math.min(st.explore_risk_pct, st.risk_per_trade_pct) : st.risk_per_trade_pct}%`, `entry cap ${st.max_entry_slip_bp} bp, up to ${st.max_leverage}× leverage`],
  ];
  return (
    <>
      <Ph code="PRTU" right={a.last_sync ? `${a.private_stream ? "streaming" : "polling"}, synced ${age(now / 1000 - a.last_sync)} ago` : ""}>{title}</Ph>
      <div className="pb tight scroll acct-body">
        <div className="acct-grid">{cells.map(([l, n, s]) => <div className="cell" key={l} data-tip={`${l}: ${s}`}><div className="l">{l}</div><div className="n">{n}</div><div className="s">{s}</div></div>)}</div>
        <div className="pos-head"><PhIcon code="PST" /><span>Positions</span><span className="muted">{a.positions.length ? `${a.positions.length} open` : ""}</span></div>
        {a.positions.length ? (
          <ul className="pos-list">
            {a.positions.map((x: any) => (
              <li key={x.symbol} className="pos">
                <div className="pos-l1">
                  <Sym symbol={x.symbol} size={18} />
                  <span className={`tag ${x.side}`}>{x.side}</span>
                  <span className="pos-sz muted">{x.size}</span>
                  <b className={`pos-pnl ${ud(x.upnl)}`}>{sg(x.upnl)}</b>
                  <button className="mini" onClick={() => closePos(x.symbol)} aria-label={`Close ${short(x.symbol)}`}>Close</button>
                </div>
                <div className="pos-l2">
                  <span><i>entry</i> {x.entry}</span><span><i>mark</i> {x.mark}</span>
                  <span><i>stop</i> {x.sl || <b className="dn">none</b>}</span><span><i>target</i> {x.tp || "—"}</span>
                </div>
              </li>
            ))}
          </ul>
        ) : <div className="empty">No open positions</div>}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- market map */
type Node = { symbol: string; value: number; area: number; x: number; y: number; w: number; h: number; [k: string]: any };
function squarify(items: any[], x: number, y: number, w: number, h: number): Node[] {
  const total = items.reduce((a, b) => a + b.value, 0);
  if (!total) return [];
  const nodes = items.map((i) => ({ ...i, area: (i.value * w * h) / total }));
  const out: Node[] = [];
  let row: any[] = [], rect = { x, y, w, h };
  const worst = (r: any[], len: number) => {
    const s = r.reduce((a, b) => a + b.area, 0), mx = Math.max(...r.map((q) => q.area)), mn = Math.min(...r.map((q) => q.area));
    return Math.max((len * len * mx) / (s * s), (s * s) / (len * len * mn));
  };
  const lay = (r: any[], rc: typeof rect) => {
    const s = r.reduce((a, b) => a + b.area, 0);
    if (rc.w >= rc.h) {
      const cw = s / rc.h;
      let cy = rc.y;
      for (const q of r) { const qh = q.area / cw; out.push({ ...q, x: rc.x, y: cy, w: cw, h: qh }); cy += qh; }
      return { x: rc.x + cw, y: rc.y, w: rc.w - cw, h: rc.h };
    }
    const rh = s / rc.w;
    let cx = rc.x;
    for (const q of r) { const qw = q.area / rh; out.push({ ...q, x: cx, y: rc.y, w: qw, h: rh }); cx += qw; }
    return { x: rc.x, y: rc.y + rh, w: rc.w, h: rc.h - rh };
  };
  for (const n of nodes) {
    const len = Math.min(rect.w, rect.h);
    if (!row.length || worst([...row, n], len) <= worst(row, len)) row.push(n);
    else { rect = lay(row, rect); row = [n]; }
  }
  if (row.length) lay(row, rect);
  return out;
}
function heat(p: number) {
  // diverging: red ← neutral grey → green, saturates at ±6%; the signed % label carries the value
  const t = Math.min(Math.abs(p) / 6, 1), n = [36, 38, 43], g = [34, 150, 106], r = [190, 68, 72], c = p >= 0 ? g : r;
  return `rgb(${n.map((v, i) => Math.round(v + (c[i] - v) * Math.pow(t, 0.75))).join(",")})`;
}
type MapSize = "40" | "100" | "200" | "0";
const MAP_SIZES: { v: MapSize; label: string }[] = [{ v: "40", label: "40" }, { v: "100", label: "100" }, { v: "200", label: "200" }, { v: "0", label: "All" }];

/** Treemap of mainnet USDT perps: area ∝ √(24h turnover), colour = 24h change, coin logo on every tile that has room.
 * The panel shows the top 40; expanded it shows the top 200, and the selector goes up to every listed perp. */
function MarketMap({ visible }: { visible: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [pick, setPick] = useState<MapSize | null>(null); // null = follow the panel (40 small, 200 expanded)
  const [expanded, setExpandedState] = useState(false);
  const [big, setBig] = useState<{ n: MapSize; rows: any[]; total: number } | null>(null);
  const [noEndpoint, setNoEndpoint] = useState(false);
  const small: any[] = useStore((s) => s.mon?.universe) || [];
  const tracked: string[] = useStore((s) => s.ov?.settings.symbols) || [];
  const n: MapSize = pick ?? (expanded ? "200" : "40");

  useEffect(() => {
    const el = host.current!;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    const pnl = el.closest(".pnl");
    const mo = new MutationObserver(() => setExpandedState(!!pnl?.classList.contains("expanded")));
    if (pnl) mo.observe(pnl, { attributes: true, attributeFilter: ["class"] });
    return () => { ro.disconnect(); mo.disconnect(); };
  }, []);

  // more than the 40 the monitor poll carries: ask the engine for the bigger list, refreshed every 10 s while shown
  useEffect(() => {
    if (n === "40" || !visible) return;
    let stop = false;
    const load = () => api(`/api/market-map?n=${n}`)
      .then((r: any) => { if (!stop) { setBig({ n, rows: r.universe, total: r.total }); setNoEndpoint(false); } })
      .catch(() => { if (!stop) setNoEndpoint(true); });
    load();
    const id = setInterval(() => { if (!document.hidden) load(); }, 10_000);
    return () => { stop = true; clearInterval(id); };
  }, [n, visible]);

  const universe = n !== "40" && big?.n === n ? big.rows : small;
  const rects = useMemo(() => (size.w && size.h ? squarify(universe.map((x) => ({ ...x, value: Math.sqrt(x.turnover24h) })), 0, 0, size.w, size.h) : []), [universe, size]);
  const title = expanded ? `Market map, 24h${universe.length ? `, top ${universe.length}` : ""}${big?.total && n !== "40" && big.n === n ? ` of ${big.total}` : ""}` : "Market map";
  return (
    <>
      <Ph code="IMAP" right={<Seg label="How many markets" value={n} onChange={(v) => setPick(v)}
        options={MAP_SIZES.map((o) => ({ ...o, tip: o.v === "0" ? "Every USDT perpetual on Bybit" : `Top ${o.label} by 24h turnover` }))} />}>
        <span data-tip="Bybit mainnet USDT perpetuals by 24h turnover. Tile size ∝ √turnover, colour = 24h change; outlined tiles are the markets the bot trades.">{title}</span>
      </Ph>
      <div className="pb map-wrap" ref={host}>
        {noEndpoint && n !== "40" && <div className="map-note">This engine only sends the top 40. Restart it to see larger maps.</div>}
        {!universe.length ? <div className="empty">Loading markets…</div> : visible && (
          <svg viewBox={`0 0 ${size.w} ${size.h}`}>
            {rects.map((r) => {
              const base = baseOf(r.symbol);
              const room = Math.min(r.w, r.h);
              const ls = room >= 44 && r.w >= 52 ? Math.round(Math.min(34, Math.max(14, room * 0.2))) : 0; // logo size, 0 = no room
              const ty = r.y + 8 + (ls ? ls + 13 : 9);
              const mine = tracked.includes(r.symbol);
              return (
                <g key={r.symbol} data-tip={`${r.symbol} · ${px(r.last)}\n24h ${sg(r.pct24h, 2, "%")}\nturnover $${usd(r.turnover24h)} · OI $${usd(r.oi_value)}\nfunding ${sg(r.funding * 100, 4, "%")}`}
                  onClick={() => { if (mine) selectSymbol(r.symbol); }} style={mine ? { cursor: "pointer" } : undefined}>
                  <rect className={mine ? "trk" : ""} x={r.x} y={r.y} width={Math.max(r.w, 0)} height={Math.max(r.h, 0)} rx={room > 24 ? 7 : 3} fill={heat(r.pct24h)} />
                  {ls > 0 && (COIN_LOGOS[base]
                    ? <image href={`/${COIN_LOGOS[base]}`} x={r.x + 8} y={r.y + 8} width={ls} height={ls} />
                    : <g><circle cx={r.x + 8 + ls / 2} cy={r.y + 8 + ls / 2} r={ls / 2} fill="rgba(0,0,0,.28)" />
                        <text className="mono" x={r.x + 8 + ls / 2} y={r.y + 8 + ls / 2 + ls * 0.16} textAnchor="middle" style={{ fontSize: ls * 0.42 }}>{base.slice(0, 2)}</text></g>)}
                  {r.w > 34 && r.h > (ls ? ls + 22 : 15) && <text x={r.x + 8} y={ty}>{short(r.symbol)}</text>}
                  {r.w > 46 && r.h > (ls ? ls + 36 : 30) && <text className="p" x={r.x + 8} y={ty + 14}>{sg(r.pct24h, 2, "%")}</text>}
                  {r.w > 90 && r.h > (ls ? ls + 52 : 46) && <text className="p" x={r.x + 8} y={ty + 28}>${usd(r.turnover24h)}</text>}
                </g>
              );
            })}
          </svg>
        )}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- order book (pushed) */
const BOOK_LEVELS = 6;

/** Cumulative depth: bids step down-left from the mid, asks step up-right. Area = resting notional. */
function DepthChart({ bids, asks, d }: { bids: [number, number][]; asks: [number, number][]; d: number }) {
  const W = 360, H = 64, PAD = 2;
  let cb = 0, ca = 0;
  const bPts = bids.map(([p, q]) => ({ p, c: (cb += p * q) }));
  const aPts = asks.map(([p, q]) => ({ p, c: (ca += p * q) }));
  const lo = bids[bids.length - 1][0], hi = asks[asks.length - 1][0];
  const mid = (bids[0][0] + asks[0][0]) / 2;
  const span = Math.max(mid - lo, hi - mid) || 1; // symmetric around the mid so sides compare fairly
  const x = (p: number) => PAD + ((p - (mid - span)) / (2 * span)) * (W - 2 * PAD);
  const mx = Math.max(cb, ca) || 1;
  const y = (c: number) => H - 12 - (c / mx) * (H - 18);
  const step = (pts: { p: number; c: number }[], start: number) => {
    let path = `M${x(start).toFixed(1)},${y(0).toFixed(1)}`;
    let prev = 0;
    for (const pt of pts) path += ` L${x(pt.p).toFixed(1)},${y(prev).toFixed(1)} L${x(pt.p).toFixed(1)},${y(pt.c).toFixed(1)}`, (prev = pt.c);
    return path;
  };
  const bidLine = step(bPts, bids[0][0]), askLine = step(aPts, asks[0][0]);
  const close = (line: string, end: number) => `${line} L${x(end).toFixed(1)},${y(0).toFixed(1)} Z`;
  return (
    <svg className="depth" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`Depth: bids $${usd(cb)} vs asks $${usd(ca)} across ${bids.length} levels`}>
      <path d={close(bidLine, bPts[bPts.length - 1].p)} fill="var(--up)" fillOpacity={0.14} />
      <path d={bidLine} fill="none" stroke="var(--up)" strokeWidth={1.5} />
      <path d={close(askLine, aPts[aPts.length - 1].p)} fill="var(--down)" fillOpacity={0.14} />
      <path d={askLine} fill="none" stroke="var(--down)" strokeWidth={1.5} />
      <line x1={x(mid)} x2={x(mid)} y1={2} y2={H - 12} stroke="var(--muted)" strokeDasharray="2 3" />
      {bPts.map((pt) => <rect key={"b" + pt.p} x={x(pt.p) - 3} y={0} width={6} height={H - 12} fill="transparent" data-tip={`≥ ${fx(pt.p, d)}: $${usd(pt.c)} bid`} />)}
      {aPts.map((pt) => <rect key={"a" + pt.p} x={x(pt.p) - 3} y={0} width={6} height={H - 12} fill="transparent" data-tip={`≤ ${fx(pt.p, d)}: $${usd(pt.c)} ask`} />)}
      <text x={PAD} y={H - 2} className="dl">{fx(mid - span, d)}</text>
      <text x={W / 2} y={H - 2} textAnchor="middle" className="dl">mid {fx(mid, d + 1)}</text>
      <text x={W - PAD} y={H - 2} textAnchor="end" className="dl">{fx(mid + span, d)}</text>
    </svg>
  );
}

function BookPanel() {
  const book = useStore((s) => s.book);
  const mkt = useStore((s) => s.mkt);
  const symbol = useStore((s) => s.symbol);
  const now = useNow(500);
  const b = book && book.symbol === symbol ? book : mkt?.symbol === symbol ? mkt?.book : null;
  const d = Math.max(String(mkt?.ticker?.lastPrice ?? "").split(".")[1]?.length || 0, 0);
  const head = <Ph code="DOM" right={book?.ts ? <span data-tip="Last push from Bybit">{age((now - book.ts) / 1000)}</span> : ""}>Order book</Ph>;
  if (!b?.bids?.length || !b?.asks?.length) return <>{head}<div className="empty">Book loading…</div></>;
  const bids = b.bids as [number, number][], asks = b.asks as [number, number][];
  const bb = bids[0][0], ba = asks[0][0], mid = (bb + ba) / 2, spr = (10000 * (ba - bb)) / mid;
  const levels = [...asks.slice(0, BOOK_LEVELS).map((l) => ["ask", ...l] as const), ...bids.slice(0, BOOK_LEVELS).map((l) => ["bid", ...l] as const)];
  const maxLvl = Math.max(...levels.map(([, p, q]) => p * q));
  let ca = 0, cb2 = 0;
  const askRows = asks.slice(0, BOOK_LEVELS).map(([p, q]) => ({ p, q, n: p * q, c: (ca += p * q) }));
  const bidRows = bids.slice(0, BOOK_LEVELS).map(([p, q]) => ({ p, q, n: p * q, c: (cb2 += p * q) }));
  const allBid = bids.reduce((a, [p, q]) => a + p * q, 0), allAsk = asks.reduce((a, [p, q]) => a + p * q, 0);
  const imb = (allBid - allAsk) / (allBid + allAsk);
  const Row = ({ side, r }: { side: "ask" | "bid"; r: { p: number; q: number; n: number; c: number } }) => {
    const wall = r.n >= maxLvl * 0.999 && levels.length > 2;
    return (
      <tr className={side} data-tip={`${side === "ask" ? "Ask" : "Bid"} ${fx(r.p, d)} · ${r.q.toLocaleString("en-US", { maximumFractionDigits: 4 })} · $${usd(r.n)}\n${(((r.p - mid) / mid) * 1e4).toFixed(1)} bp from mid · cumulative $${usd(r.c)}`}>
        <td className={side === "ask" ? "dn" : "up"}>{fx(r.p, d)}</td>
        <td className="r">{r.q.toLocaleString("en-US", { maximumFractionDigits: 4 })}</td>
        <td className="r lvl">{wall && <span className="wall">wall</span>}{usd(r.n)}<span className="bar" style={{ width: `${((100 * r.n) / maxLvl).toFixed(1)}%` }} /></td>
        <td className="r muted">{usd(r.c)}</td>
      </tr>
    );
  };
  return (
    <>
      {head}
      <div className="pb tight scroll ladder">
        <DepthChart bids={bids} asks={asks} d={d} />
        <div className="imb" data-tip={`All ${bids.length + asks.length} pushed levels: bids $${usd(allBid)} vs asks $${usd(allAsk)}`}>
          <span className="up">bid ${usd(allBid)}</span>
          <span className="imb-bar"><span style={{ width: `${(50 + 50 * imb).toFixed(1)}%` }} /></span>
          <span className="dn">${usd(allAsk)} ask</span>
          <span className="muted">{sg(imb * 100, 0, "%")}</span>
        </div>
        <table className="t">
          <thead><tr><th>Price</th><th className="r">Size</th><th className="r">Level $</th><th className="r">Cum $</th></tr></thead>
          <tbody>
            {[...askRows].reverse().map((r) => <Row key={"a" + r.p} side="ask" r={r} />)}
            <tr className="mid"><td colSpan={4}>spread {fx(ba - bb, d)} ({fx(spr, 2)} bp), mid {fx(mid, d + 1)}</td></tr>
            {bidRows.map((r) => <Row key={"b" + r.p} side="bid" r={r} />)}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- news, tape */
function NewsMini() {
  const items: any[] = useStore((s) => s.mon?.news) || [];
  const forecasts = useStore((s) => s.news.forecasts);
  const symbol = useStore((s) => s.symbol);
  const fc: Record<string, any> = {};
  for (const f of forecasts || []) if (f.probs_json) fc[f.id.split(":")[0]] = f;
  const src: Record<string, string> = { CoinDesk: "CD", Cointelegraph: "CT", Decrypt: "DC" };
  if (!items.length) return <div className="empty">Loading headlines…</div>;
  return (
    <table className="t"><tbody>
      {items.map((h) => {
        const f = fc[h.id];
        const lean = f ? f.probs_json.slice(4).reduce((a: number, b: number) => a + b, 0) - f.probs_json.slice(0, 3).reduce((a: number, b: number) => a + b, 0) : null;
        return (
          <tr key={h.id} className="click" onClick={() => h.link && window.open(h.link, "_blank", "noopener")}>
            <td className="muted">{h.published_ts ? hhmm(h.published_ts) : "--:--"}</td><td className="amb">{src[h.source] || "NW"}</td>
            <td className="wrap" style={h.symbols.includes(symbol) ? { color: "var(--amber)" } : undefined}>
              {h.title} {h.symbols.map((s: string) => <span key={s} className="tag flat">{short(s)}</span>)}
              {f && <> <span className={`tag ${lean! > 0 ? "long" : lean! < 0 ? "short" : "flat"}`} data-tip={`Kairos news_return 15m: up−down ${sg(lean, 2)}`}>K {sg(lean, 2)}</span></>}
            </td>
          </tr>
        );
      })}
    </tbody></table>
  );
}

function Tape() {
  const t: any[] = useStore((s) => s.mon?.tape) || [];
  return (
    <>
      <Ph code="ALRT" right={`${t.length} events`}>Liquidations and block prints</Ph>
      <div className="pb tight scroll">
        {t.length ? (
          <table className="t">
            <thead><tr><th>Time</th><th>Market</th><th>Type</th><th>Side</th><th className="r">Notional $</th><th className="r">Price</th></tr></thead>
            <tbody>
              {t.map((e, i) => {
                const bull = e.side === "BUY" || e.side === "SHORT"; // a short liquidation is forced buying
                return (
                  <tr key={i}><td className="muted">{hhmmss(e.ts)}</td><td className="sym"><Sym symbol={e.symbol} size={14} /></td>
                    <td>{e.kind === "LIQ" ? <span className="tag amb">LIQ</span> : <span className="tag flat">BLOCK</span>}</td>
                    <td className={bull ? "up" : "dn"}>{e.kind === "LIQ" ? `${e.side} LIQ` : e.side}</td><td className="r">{usd(e.notional)}</td><td className="r muted">{e.price ? px(e.price) : ""}</td></tr>
                );
              })}
            </tbody>
          </table>
        ) : <div className="empty">No liquidations or block prints since the stream started</div>}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- stats */
// Pairs are direct grid children (a wrapper with display: contents would drop their padding and borders).
const KV = ({ rows }: { rows: [string, React.ReactNode, string?][] }) => (
  <div className="kv">{rows.map(([k, v, tp]) => <Fragment key={k}><div className="k" data-tip={tp}>{k}</div><div className="v">{v}</div></Fragment>)}</div>
);
export { KV };

function Stats() {
  const m = useStore((s) => s.mkt);
  const showState = () => m?.state_preview && setState({ modal: { kind: "state", data: { symbol: m.symbol, preview: m.state_preview } } });
  if (!m) return <><Ph code="DES">Derivatives and flow</Ph><div className="empty">Loading…</div></>;
  const t = m.ticker, st = m.state_preview?.state || {}, dv = st.derivatives || {}, ob = st.order_book || {}, of = st.order_flow || {};
  const fr = isNum(t.fundingRate) ? 100 * t.fundingRate : null;
  const collecting = <span className="muted">collecting…</span>;
  const flow = (f: any, lab: string): [string, React.ReactNode, string?] => f
    ? [lab, <span key={lab}><span className={ud(f.net_taker_flow_usd)}>{sg(f.net_taker_flow_usd / 1e3, 1, "K")}</span> · {fx(100 * f.taker_buy_share, 1)}% buy</span>]
    : [lab, collecting, "Not enough stream history yet: reported as unknown, never as zero"];
  const liq = (a: any, b: any) => (a === null || a === undefined ? collecting : <><span className="dn">{usd(a)}</span> / <span className="up">{usd(b)}</span></>);
  const n = m.state_preview ? JSON.stringify(m.state_preview.state).length : 0;
  return (
    <>
      <Ph code="DES" right={n ? <button className="mini" onClick={showState}>Model input, ≈{Math.round(n / 3.6)} tokens</button> : ""}><Sym symbol={m.symbol} size={16} /> derivatives, flow and trend</Ph>
      <div className="pb tight scroll">
        <div className="kvcols">
          <div><div className="colh">Derivatives</div><KV rows={[
            ["Funding 8h", <span key="f" className={ud(fr)}>{sg(fr, 4, "%")}</span>, "Positive: longs pay shorts"], ["Annualised", sg(isNum(fr) ? fr * 3 * 365 : null, 1, "%")],
            ["Mark − index", sg(dv.mark_minus_index_bp, 2, " bp")], ["Open interest", "$" + usd(Number(t.openInterestValue))],
            ["OI Δ 1h / 4h", <span key="oi"><span className={ud(dv.open_interest_change_1h_pct)}>{sg(dv.open_interest_change_1h_pct, 2, "%")}</span> / <span className={ud(dv.open_interest_change_4h_pct)}>{sg(dv.open_interest_change_4h_pct, 2, "%")}</span></span>],
            ["Accounts long", isNum(dv.accounts_long_share) ? `${fx(100 * dv.accounts_long_share, 1)}% (${sg(100 * (dv.accounts_long_share_change_1h || 0), 1)} 1h)` : "—"],
          ]} /></div>
          <div><div className="colh">Taker flow and liquidations</div><KV rows={[
            flow(of.last_5m, "Net taker 5m"), flow(of.last_15m, "Net taker 15m"), flow(of.last_60m, "Net taker 60m"),
            ["Liq L/S 15m", liq(dv.long_liquidations_15m_usd, dv.short_liquidations_15m_usd), "Long liquidations (forced selling) / short liquidations (forced buying)"],
            ["Liq L/S 60m", liq(dv.long_liquidations_60m_usd, dv.short_liquidations_60m_usd)],
            ["Stream coverage", `${of.stream_coverage_minutes ?? "—"} min`],
          ]} /></div>
          <div><div className="colh">Book, volatility, context</div><KV rows={[
            ["Spread", ob.available === false ? "stale" : `${fx(ob.spread_bp, 2)} bp`],
            ["Depth ≤10bp B/A", ob.available === false ? "—" : `${usd(ob.bid_depth_within_10bp_usd)} / ${usd(ob.ask_depth_within_10bp_usd)}`],
            ["24h range pos", isNum(st.range_position_24h_0_to_1) ? fx(st.range_position_24h_0_to_1, 2) : "—", "0 = at 24h low, 1 = at high; outside 0..1 = breakout"],
            ["BTC 1h / 4h", st.cross_market?.btc_return_1h_pct !== undefined ? `${sg(st.cross_market.btc_return_1h_pct, 2, "%")} / ${sg(st.cross_market.btc_return_4h_pct, 2, "%")}` : "—"],
            ["Bracket SL / TP", m.state_preview ? `${fx(m.state_preview.meta.sl_bp / 100, 2)}% / ${fx(m.state_preview.meta.tp_bp / 100, 2)}%` : "—"],
            ["Horizon / cost", m.state_preview ? `${m.state_preview.meta.horizon_min}m / ${fx(m.state_preview.meta.cost_bp, 1)}bp` : "—"],
          ]} /></div>
        </div>
        <table className="t">
          <thead><tr><th>TF</th><th className="r">Ret 12b %</th><th className="r">vs EMA20</th><th className="r">vs EMA50</th><th className="r">vs EMA200</th><th className="r">RSI14</th><th className="r">ADX14</th><th className="r">+DI − −DI</th><th className="r">ATR %</th><th className="r">BB pos</th><th className="r">Vol ×</th></tr></thead>
          <tbody>
            {Object.entries<any>(st.trend_by_timeframe || {}).map(([tf, b]) => (
              <tr key={tf}><td className="amb">{tf}</td>
                <td className={`r ${ud(b.return_last_12_bars_pct)}`}>{sg(b.return_last_12_bars_pct, 2)}</td><td className={`r ${ud(b.price_vs_ema20_pct)}`}>{sg(b.price_vs_ema20_pct, 2)}</td>
                <td className={`r ${ud(b.price_vs_ema50_pct)}`}>{sg(b.price_vs_ema50_pct, 2)}</td><td className={`r ${ud(b.price_vs_ema200_pct)}`}>{sg(b.price_vs_ema200_pct, 2)}</td>
                <td className="r">{fx(b.rsi14, 1)}</td><td className="r">{fx(b.adx14, 1)}</td><td className={`r ${ud(b.di_plus_minus_di)}`}>{sg(b.di_plus_minus_di, 1)}</td>
                <td className="r">{fx(b.atr14_pct, 3)}</td><td className="r">{fx(b.bollinger_position_0_to_1, 2)}</td><td className="r">{fx(b.volume_vs_20bar_avg, 2)}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- blotter */
export async function openDecision(id: number | string) {
  setState({ modal: { kind: "decision", data: await api(`/api/decision/${id}`) } });
}

function Blotter() {
  const decisions = useStore((s) => s.decisions);
  const symbol = useStore((s) => s.symbol);
  const horizon = useStore((s) => s.ov?.settings.horizon_minutes);
  const rows = decisions.slice(0, 150);
  if (!rows.length) return <div className="empty">No model decisions yet</div>;
  return (
    <table className="t">
      <thead><tr><th>Time</th><th>Market</th><th>Signal</th><th className="r">Price</th><th className="r">EV long</th><th className="r">EV short</th><th className="r">Setup</th><th className="r">SL/TP %</th><th className="r">ms</th><th>Outcome (labelled)</th><th>Reason / gates</th></tr></thead>
      <tbody>
        {rows.map((d) => {
          const l = d.label;
          return (
            <tr key={d.id} className={`click ${d.symbol === symbol ? "sel" : ""}`} onClick={() => openDecision(d.id)}>
              <td className="muted">{dt(d.ts)}</td><td className="sym"><Sym symbol={d.symbol} size={14} /></td>
              <td>{d.error_kind ? <span className="tag err">{d.error_kind}</span> : <><span className={`tag ${d.action}`}>{d.action || "—"}</span>{d.exploration && <> <span className="tag xp">explore</span></>}{d.probe && <> <span className="tag flat">{d.probe}</span></>}</>}</td>
              <td className="r">{isNum(d.price) ? String(d.price) : "—"}</td>
              <td className={`r ${ud(d.ev_long_r)}`}>{sg(d.ev_long_r, 3)}</td><td className={`r ${ud(d.ev_short_r)}`}>{sg(d.ev_short_r, 3)}</td>
              <td className="r">{fx(d.setup_quality, 2)}</td><td className="r">{fx(d.sl_bp / 100, 2)}/{fx(d.tp_bp / 100, 2)}</td><td className="r">{fx(d.latency_ms, 0)}</td>
              <td>{l && !l.unlabelable ? <>L {l.long.outcome.replace("_first", "").toUpperCase()} · S {l.short.outcome.replace("_first", "").toUpperCase()} · <span className={ud(l.return_pct)}>{sg(l.return_pct, 2, "%")}</span></> : <span className="muted">pending, scored after {horizon}m</span>}</td>
              <td className="muted wrap">{d.reason || d.error_msg || ""}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export { cssVar };
