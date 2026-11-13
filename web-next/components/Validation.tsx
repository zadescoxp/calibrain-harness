"use client";
// VALID: is there an edge, where does it live, does it survive costs, and is it still there?
// Every model-quality number here uses independent windows only (see /api/validation).
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { setState, useStore } from "@/lib/store";
import { fx, isNum, sg, short, ud } from "@/lib/format";
import { KV } from "./Monitor";

const Panel = ({ code, title, span2, tight, children }: { code: string; title: string; span2?: boolean; tight?: boolean; children: React.ReactNode }) => (
  <section className={`pnl${span2 ? " span2" : ""}`}><div className="ph"><span className="code">{code}</span>{title}</div><div className={`pb${tight ? " tight" : ""}`}>{children}</div></section>
);

const VERDICT: Record<string, string> = { skill: "up", "no skill": "dn", undecided: "amb", "not enough data": "muted" };
const ci = (c: any) => (c && isNum(c[0]) ? `[${sg(c[0], 2)}, ${sg(c[1], 2)}]` : "—");
const dist = (d: any, unit = "bp") => (d ? `${sg(d.mean, 1)}${unit} mean · p50 ${sg(d.p50, 1)} · p90 ${sg(d.p90, 1)} (n=${d.n})` : "—");

function Verdict({ v }: { v?: string }) {
  return <span className={`vchip ${VERDICT[v || ""] || ""}`}>{(v || "—").toUpperCase()}</span>;
}

/** Rolling skill as a sparkline: above the zero line = the model beat the base rates in that window. */
function Spark({ xs }: { xs: (number | null)[] }) {
  const v = xs.filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  if (v.length < 2) return <span className="muted">not enough windows</span>;
  const W = 220, H = 36, lo = Math.min(-0.2, ...v), hi = Math.max(0.2, ...v);
  const y = (x: number) => H - 2 - ((x - lo) / (hi - lo)) * (H - 4);
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * W},${y(x)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} className="spark">
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} stroke="var(--line)" strokeDasharray="3 3" />
      <polyline points={pts} fill="none" stroke="var(--amber)" strokeWidth={1.5} />
      <circle cx={W} cy={y(v[v.length - 1])} r={2.5} fill={v[v.length - 1] >= 0 ? "var(--up)" : "var(--down)"} />
    </svg>
  );
}

function GroupTable({ rows, label }: { rows: any[]; label: string }) {
  if (!rows?.length) return <div className="empty">Needs labelled decisions</div>;
  return (
    <table className="t">
      <thead><tr><th>{label}</th><th className="r">Windows</th><th className="r">Bracket skill</th><th className="r">Model side R</th><th className="r">Trades</th><th className="r">P&L</th><th className="r">Win</th><th className="r">Avg R</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={String(r.key)}><td className="amb">{String(r.key)}</td><td className="r">{r.independent_windows}</td>
          <td className={`r ${ud(r.bracket_skill)}`}>{sg(r.bracket_skill, 3)}</td><td className={`r ${ud(r.model_side_mean_r)}`}>{sg(r.model_side_mean_r, 2)}</td>
          <td className="r">{r.trades}</td><td className={`r ${ud(r.trade_pnl)}`}>{r.trades ? sg(r.trade_pnl) : "—"}</td>
          <td className="r">{isNum(r.trade_win_rate) ? `${(100 * r.trade_win_rate).toFixed(0)}%` : "—"}</td><td className={`r ${ud(r.trade_avg_r)}`}>{sg(r.trade_avg_r, 2)}</td></tr>
      ))}</tbody>
    </table>
  );
}

/** 24 UTC hours as bars of the model side's realised R: where in the day any edge lives. */
function HourBars({ rows }: { rows: any[] }) {
  const by: Record<number, any> = {};
  (rows || []).forEach((r) => (by[r.key] = r));
  const vals: (number | null)[] = Array.from({ length: 24 }, (_, h) => (isNum(by[h]?.model_side_mean_r) ? Number(by[h].model_side_mean_r) : null));
  const mx = Math.max(0.2, ...vals.filter((x): x is number => x !== null).map((x) => Math.abs(x)));
  const W = 480, H = 90, bw = W / 24, z = H / 2;
  return (
    <svg viewBox={`0 0 ${W} ${H + 14}`} width="100%">
      <line x1={0} x2={W} y1={z} y2={z} stroke="var(--line)" />
      {[0, 7, 13, 21].map((h) => <line key={h} x1={h * bw} x2={h * bw} y1={0} y2={H} stroke="var(--line-2)" strokeDasharray="2 3" />)}
      {vals.map((v, h) => {
        if (v === null) return null;
        const hgt = (Math.abs(v) / mx) * (z - 4);
        return <rect key={h} x={h * bw + 2} y={v >= 0 ? z - hgt : z} width={bw - 4} height={Math.max(1, hgt)} fill={v >= 0 ? "var(--up)" : "var(--down)"}
          data-tip={`${String(h).padStart(2, "0")}:00 UTC · model side ${sg(v, 2)}R · ${by[h].independent_windows} windows · skill ${sg(by[h].bracket_skill, 3)}`} />;
      })}
      {[0, 6, 12, 18, 23].map((h) => <text key={h} x={h * bw + bw / 2} y={H + 11} textAnchor="middle">{String(h).padStart(2, "0")}</text>)}
    </svg>
  );
}

export function ValidationView() {
  const st = useStore((s) => s.ov?.settings);
  const models = st?.models || {};
  const [f, setF] = useState({ model: "", run: "", tf: "" });
  const [v, setV] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    const q = new URLSearchParams();
    Object.entries(f).forEach(([k, val]) => val && q.set(k, val));
    try {
      setV(await api("/api/validation?" + q));
      setErr(null);
    } catch (e: any) {
      setErr(e.message);
    }
  }, [f]);
  useEffect(() => { load(); }, [load]);
  const toolbar = (
    <div className="toolbar">
      <span className="code">VALID</span><b>Validation · independent windows</b>
      <select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}><option value="">ACTIVE MODEL</option>{Object.entries<any>(models).map(([p, m]) => <option key={p} value={p}>{m.label.toUpperCase()}</option>)}</select>
      <select value={f.tf} onChange={(e) => setF({ ...f, tf: e.target.value })}><option value="">ACTIVE TRADE TF</option>{st && Object.entries<any>(st.tf_profiles).map(([tf, p]) => <option key={tf} value={tf}>TRADE TF {p.label}</option>)}</select>
      <input className="runin" placeholder="RUN #" value={f.run} onChange={(e) => setF({ ...f, run: e.target.value.replace(/\D/g, "") })} />
      <button className="kbtn" onClick={() => load()}>REFRESH</button>
      <button className="kbtn amber" onClick={() => setState({ modal: { kind: "strategy" } })}>STRATEGY</button>
    </div>
  );
  if (err) return <main className="view on">{toolbar}<div className="empty">{err}</div></main>;
  if (!v) return <main className="view on">{toolbar}<div className="empty">Computing validation…</div></main>;
  const ov = v.overall || {}, wf = v.walk_forward || {}, ec = v.edge_vs_cost || {}, dr = v.drift || {}, ex = v.execution || {}, cal = v.calibration || {};
  const em = ec.model_side_all_bars || {};
  const cells: [string, React.ReactNode, string][] = [
    ["Independent windows", v.independent_windows, `of ${v.labelled} labelled · ${v.decisions} decisions`],
    ["Bracket skill", <span key="b" className={ud(ov.bracket?.skill)}>{sg(ov.bracket?.skill, 3)}</span>, `90% ${ci(ov.bracket?.ci90)} · ${ov.bracket?.verdict ?? "—"}`],
    ["Big-move skill", <span key="m" className={ud(ov.big_move?.skill)}>{sg(ov.big_move?.skill, 3)}</span>, `90% ${ci(ov.big_move?.ci90)} · ${ov.big_move?.verdict ?? "—"}`],
    ["Out-of-sample skill", <span key="o" className={ud(wf.oos_bracket_skill)}>{sg(wf.oos_bracket_skill, 3)}</span>, `${wf.folds?.length || 0} walk-forward folds`],
    ["Gross edge", <span key="g" className={ud(em.gross_edge_bp)}>{isNum(em.gross_edge_bp) ? sg(em.gross_edge_bp, 1) + "bp" : "—"}</span>, `cost ${fx(em.cost_bp, 1)}bp · net ${sg(em.net_bp, 1)}bp`],
    ["Drift", <span key="d" className={dr.alarm ? "dn" : "up"}>{dr.alarm ? "ALARM" : "OK"}</span>, dr.alarm ? `on ${(dr.questions || []).join(", ")}` : "Page-Hinkley on Brier gap"],
  ];
  const syms = Object.entries<any>(v.per_symbol || {});
  return (
    <main className="view on">
      {toolbar}
      <div className="strip">{cells.map(([l, n, s]) => <div className="cell" key={l}><div className="l">{l}</div><div className="n">{n}</div><div className="s">{s}</div></div>)}</div>
      <div className="grid-eval">
        <Panel code="SYM" title="Skill against base rates · per symbol (90% bootstrap interval)" span2 tight>
          {syms.length ? (
            <table className="t">
              <thead><tr><th>Symbol</th><th className="r">Windows</th><th className="r">Bracket skill</th><th className="r">Interval</th><th>Verdict</th>
                <th className="r">Direction</th><th className="r">Big move</th><th className="r">Return dist.</th><th>Strict trading</th></tr></thead>
              <tbody>{syms.map(([sym, b]) => (
                <tr key={sym}><td className="sym">{short(sym)}</td><td className="r">{b.bracket.independent_windows}</td>
                  <td className={`r ${ud(b.bracket.skill)}`}>{sg(b.bracket.skill, 3)}</td><td className="r">{ci(b.bracket.ci90)}</td><td><Verdict v={b.bracket.verdict} /></td>
                  <td className={`r ${ud(b.direction?.skill)}`}>{sg(b.direction?.skill, 3)}</td><td className={`r ${ud(b.big_move?.skill)}`}>{sg(b.big_move?.skill, 3)}</td>
                  <td className={`r ${ud(b.return_distribution?.skill)}`}>{sg(b.return_distribution?.skill, 3)}</td>
                  <td className="wrap">{v.validated?.[sym]?.ok ? <span className="up">VALIDATED</span> : <span className="muted">{v.validated?.[sym]?.why}</span>}</td></tr>
              ))}</tbody>
            </table>
          ) : <div className="empty">Needs labelled decisions</div>}
          <div className="muted" style={{ padding: "4px 8px" }}>Skill = 1 − Brier(model) / Brier(base rates), on windows that do not overlap. A symbol is validated, and strict trades are allowed on it, only when the whole 90% interval is above 0.</div>
        </Panel>
        <Panel code="HOUR" title="Model side realised R by hour (UTC) · sessions: Asia 00, Europe 07, US 13, late US 21"><HourBars rows={v.by_hour_utc} /></Panel>
        <Panel code="SESS" title="By session" tight><GroupTable rows={v.by_session} label="Session" /></Panel>
        <Panel code="RGM" title="By regime (GARCH volatility × trend)" tight><GroupTable rows={v.by_regime} label="Regime" /></Panel>
        <Panel code="EVT" title="By market event on the decision bar" tight><GroupTable rows={v.by_event} label="Event" /></Panel>
        <Panel code="WF" title="Walk-forward · weights fitted before each fold, scored on it" span2 tight>
          {wf.folds?.length ? (
            <table className="t">
              <thead><tr><th>Fold</th><th className="r">Train</th><th className="r">Test</th><th className="r">Model weight</th><th className="r">OOS skill</th><th className="r">Policy trades</th><th className="r">Mean R</th><th className="r">Base-only trades</th><th className="r">Base-only R</th></tr></thead>
              <tbody>{wf.folds.map((r: any) => (
                <tr key={r.fold}><td className="amb">{r.fold}</td><td className="r">{r.train_windows}</td><td className="r">{r.test_windows}</td><td className="r">{fx(r.weight_model, 2)}</td>
                  <td className={`r ${ud(r.oos_bracket_skill)}`}>{sg(r.oos_bracket_skill, 3)}</td><td className="r">{r.policy_trades}</td><td className={`r ${ud(r.policy_mean_r)}`}>{sg(r.policy_mean_r, 2)}</td>
                  <td className="r">{r.base_only_trades}</td><td className={`r ${ud(r.base_only_mean_r)}`}>{sg(r.base_only_mean_r, 2)}</td></tr>
              ))}</tbody>
            </table>
          ) : <div className="empty">{wf.note || "Needs labelled decisions"}</div>}
        </Panel>
        <Panel code="COST" title="Edge vs execution cost" tight>
          <table className="t">
            <thead><tr><th>Set</th><th className="r">n</th><th className="r">Gross</th><th className="r">Cost</th><th className="r">Net</th><th className="r">Gross R</th><th className="r">Net R</th></tr></thead>
            <tbody>{Object.entries<any>(ec).map(([k, r]) => (
              <tr key={k}><td className="amb">{k.replace(/_/g, " ")}</td><td className="r">{r.n}</td><td className={`r ${ud(r.gross_edge_bp)}`}>{r.n ? sg(r.gross_edge_bp, 1) + "bp" : "—"}</td>
                <td className="r">{r.n ? fx(r.cost_bp, 1) + "bp" : "—"}</td><td className={`r ${ud(r.net_bp)}`}>{r.n ? sg(r.net_bp, 1) + "bp" : "—"}</td>
                <td className={`r ${ud(r.gross_edge_r)}`}>{sg(r.gross_edge_r, 3)}</td><td className={`r ${ud(r.net_r)}`}>{sg(r.net_r, 3)}</td></tr>
            ))}</tbody>
          </table>
          <div className="muted" style={{ padding: "4px 8px" }}>Gross = what the model's side earned before fees and slippage. An edge is tradeable only if gross &gt; cost. Live cost model: target {fx(v.live_cost_model?.target_bp, 1)}bp · stop {fx(v.live_cost_model?.stop_bp, 1)}bp · time exit {fx(v.live_cost_model?.time_bp, 1)}bp ({v.live_cost_model?.entry_mode} entries).</div>
        </Panel>
        <Panel code="DRFT" title="Drift · rolling skill (30 windows)" tight>
          <table className="t">
            <thead><tr><th>Question</th><th>Rolling skill</th><th className="r">Recent</th><th className="r">PH / limit</th><th>Status</th></tr></thead>
            <tbody>{["bracket", "direction", "big_move"].map((g) => dr[g] && (
              <tr key={g}><td className="amb">{g}</td><td><Spark xs={dr[g].rolling_skill || []} /></td><td className={`r ${ud(dr[g].recent_skill)}`}>{sg(dr[g].recent_skill, 3)}</td>
                <td className="r">{fx(dr[g].ph, 2)} / {fx(dr[g].threshold, 1)}</td><td>{dr[g].alarm ? <span className="dn">ALARM</span> : <span className="up">OK</span>}</td></tr>
            ))}</tbody>
          </table>
          <div className="muted" style={{ padding: "4px 8px" }}>On alarm the model's weight drops to 0 for that question (DRIFT ACTION base), or trading pauses (pause).</div>
        </Panel>
        <Panel code="EXEC" title="Execution quality" tight><KV rows={[
          ["Implementation shortfall", dist(ex.implementation_shortfall_bp), "Fill vs the mainnet price the decision was made on; includes testnet divergence"],
          ["Entry slip vs testnet", dist(ex.entry_slip_vs_testnet_bp)], ["Stop exit slip", dist(ex.stop_exit_slip_bp)],
          ["Fees", dist(ex.fees_bp)], ["Impact proxy", dist(ex.impact_proxy_bp), "Our notional ÷ mainnet depth within 10bp × 10bp"],
          ["Markout +1m", dist(ex.markout_bp_after_entry?.["1"]), "Price move in our favour after entry"], ["Markout +5m", dist(ex.markout_bp_after_entry?.["5"])],
          ["Markout +15m", dist(ex.markout_bp_after_entry?.["15"])],
          ["Maker entries", ex.maker?.orders ? `${ex.maker.filled}/${ex.maker.orders} filled (${fx(100 * (ex.maker.fill_rate || 0), 0)}%) · missed ${ex.maker.missed_trades}, their R ${sg(ex.maker.missed_mean_r, 2)}` : "—"],
        ]} /></Panel>
        <Panel code="CAL" title="Calibration weights in use (independent windows)" tight>
          <table className="t">
            <thead><tr><th>Group</th><th className="r">Windows</th><th className="r">Brier model</th><th className="r">Brier base</th><th className="r">Skill</th><th>Weights</th></tr></thead>
            <tbody>{Object.entries<any>(cal.report || {}).map(([g, r]) => r && (
              <tr key={g}><td className="amb">{g}</td><td className="r">{r.independent_windows}</td><td className="r">{fx(r.brier_model ?? r.brier_kairos, 4)}</td><td className="r">{fx(r.brier_base_rates, 4)}</td>
                <td className={`r ${ud(r.kairos_skill_vs_base)}`}>{sg(r.kairos_skill_vs_base, 3)}</td>
                <td>{Object.entries<any>(r.weights || {}).map(([k, w]) => `${k} ${fx(w, 2)}`).join(" · ")}{r.fitted ? "" : " (prior)"}{cal.overrides?.[g === "bracket" ? "long_bracket" : g] ? " · drift: 0" : ""}</td></tr>
            ))}</tbody>
          </table>
        </Panel>
      </div>
    </main>
  );
}
