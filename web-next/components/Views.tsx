"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { loadNews } from "@/lib/data";
import { useStore } from "@/lib/store";
import { dt, fx, isNum, sg, short, ud } from "@/lib/format";
import { KV, openDecision } from "./Monitor";

/* ---------------------------------------------------------------- charts for EVAL (hand-drawn SVG) */
function Reliability({ cal, horizon }: { cal: any; horizon?: number }) {
  if (!cal?.bins?.length) return <div className="empty">Needs labelled decisions (each is scored {horizon || 120} min after it is made)</div>;
  const W = 420, H = 250, P = 34, x = (v: number) => P + v * (W - 2 * P), y = (v: number) => H - P - v * (H - 2 * P), mxN = Math.max(...cal.bins.map((b: any) => b.n));
  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={y(0)} y2={y(1)} stroke="#151515" /><line x1={x(0)} x2={x(1)} y1={y(t)} y2={y(t)} stroke="#151515" />
            <text x={x(t)} y={H - P + 14} textAnchor="middle">{t * 100}%</text><text x={P - 6} y={y(t) + 3} textAnchor="end">{t * 100}%</text>
          </g>
        ))}
        <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#5a5a5a" strokeDasharray="4 4" />
        {cal.bins.map((b: any) => (
          <circle key={b.bin} cx={x(b.mean_pred)} cy={y(b.observed)} r={4 + 6 * Math.sqrt(b.n / mxN)} fill="var(--amber)" stroke="#000" strokeWidth={2}
            data-tip={`predicted ${(100 * b.mean_pred).toFixed(1)}% → observed ${(100 * b.observed).toFixed(1)}%\nn = ${b.n}`} />
        ))}
        <text x={W / 2} y={H - 3} textAnchor="middle">predicted probability → · observed frequency ↑</text>
      </svg>
      <div className="muted">On the dashed line = calibrated · ECE {fx(cal.ece, 3)} · dot size = sample count</div>
    </>
  );
}

function HBars({ items, vk, lk, unit = "", signed = true }: { items: any[]; vk: string; lk: string; unit?: string; signed?: boolean }) {
  if (!items?.length) return <div className="empty">Needs labelled decisions</div>;
  const W = 420, rh = 24, H = items.length * rh + 8, L = 120, R = 56, mx = Math.max(0.01, ...items.map((i) => Math.abs(i[vk])));
  const z = signed ? L + (W - L - R) / 2 : L, sc = (signed ? (W - L - R) / 2 : W - L - R) / mx;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%">
      {signed && <line x1={z} x2={z} y1={0} y2={H} stroke="#5a5a5a" />}
      {items.map((it, i) => {
        const v = it[vk], w = Math.abs(v) * sc, x0 = v >= 0 ? z : z - w, yy = 4 + i * rh;
        return (
          <g key={i}>
            <text x={L - 6} y={yy + 13} textAnchor="end">{it[lk]}</text>
            <rect x={x0} y={yy + 3} width={Math.max(w, 1)} height={14} fill={signed ? (v >= 0 ? "var(--up)" : "var(--down)") : "var(--amber)"}
              data-tip={`${it[lk]}: ${fx(v, 3)}${unit}${it.n !== undefined ? ` · n=${it.n}` : ""}${it.win_rate !== undefined ? ` · win ${(100 * it.win_rate).toFixed(0)}%` : ""}`} />
            <text x={W - 4} y={yy + 13} textAnchor="end">{signed ? sg(v, 2) : fx(v, 0)}{unit}</text>
          </g>
        );
      })}
    </svg>
  );
}

const Panel = ({ code, title, span2, tight, children }: { code: string; title: string; span2?: boolean; tight?: boolean; children: React.ReactNode }) => (
  <section className={`pnl${span2 ? " span2" : ""}`}><div className="ph"><span className="code">{code}</span>{title}</div><div className={`pb${tight ? " tight" : ""}`}>{children}</div></section>
);

const dist = (d: any, unit = "bp") => (d ? `${sg(d.mean, 1)}${unit} mean · p90 ${sg(d.p90, 1)} · max ${sg(d.max, 1)} (n=${d.n})` : "—");

export function EvalView() {
  const st = useStore((s) => s.ov?.settings);
  const [f, setF] = useState({ symbol: "", tf: "", hours: "", qset: "", run: "", model: "" });
  const models = useStore((s) => s.ov?.settings.models) || {};
  const runsData = useRuns(0);
  const [m, setM] = useState<any>(null);
  const load = useCallback(async () => {
    const q = new URLSearchParams();
    if (f.tf) q.set("tf", f.tf);
    if (f.symbol) q.set("symbol", f.symbol);
    if (f.hours) q.set("hours", f.hours);
    if (f.qset) q.set("qset", f.qset);
    if (f.run) q.set("run", f.run);
    if (f.model) q.set("model", f.model);
    setM(await api("/api/metrics?" + q));
  }, [f]);
  useEffect(() => { load().catch(() => {}); }, [load]);
  const report = async () => {
    const md = await api<string>("/api/report" + (f.model ? `?model=${f.model}` : ""));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = `model-report-${f.model || "all"}-${new Date().toISOString().slice(0, 16)}.md`;
    a.click();
  };
  const toolbar = (
    <div className="toolbar">
      <span className="code">EVAL</span><b>Model evaluation</b>
      <select value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value })}><option value="">ALL SYMBOLS</option>{st?.symbols.map((s: string) => <option key={s} value={s}>{s}</option>)}</select>
      <select value={f.tf} onChange={(e) => setF({ ...f, tf: e.target.value })}><option value="">ALL TRADE TFs</option>{st && Object.entries<any>(st.tf_profiles).map(([tf, p]) => <option key={tf} value={tf}>TRADE TF {p.label}</option>)}</select>
      <select value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })}><option value="">ALL MODELS</option>{Object.entries<any>(models).map(([p, m]) => <option key={p} value={p}>{m.label.toUpperCase()}</option>)}</select>
      <select value={f.run} onChange={(e) => setF({ ...f, run: e.target.value })}><option value="">ALL RUNS</option>{runsData?.runs.map((r: any) => <option key={r.id} value={String(r.id)}>RUN {r.id}{r.current ? " (LIVE)" : ""}</option>)}</select>
      <select value={f.qset} onChange={(e) => setF({ ...f, qset: e.target.value })} data-tip="Question set: v2 adds base-rate anchoring, direction and big_move"><option value="perp-v2">QUESTIONS v2</option><option value="perp-v1">QUESTIONS v1</option><option value="">ALL QUESTION SETS</option></select>
      <select value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })}><option value="">ALL TIME</option><option value="24">LAST 24H</option><option value="6">LAST 6H</option><option value="4">LAST 4H</option></select>
      <button className="kbtn" onClick={() => load()}>REFRESH</button>
      <button className="kbtn amber" onClick={report}>EXPORT MINARA REPORT</button>
    </div>
  );
  if (!m) return <main className="view on">{toolbar}<div className="empty">Computing metrics…</div></main>;
  const mq = m.model_quality, ops = m.ops, edge = m.edge, cons = m.consistency, t = m.trading, ex = t.execution || {};
  const pct = (x: any) => (isNum(x) ? (100 * Number(x)).toFixed(0) + "%" : "—");
  const cells: [string, React.ReactNode, string][] = [
    ["Model requests", ops.requests, `${ops.ok} ok · err ${fx(100 * (ops.error_rate || 0), 1)}%`], ["Labelled", m.labelled, "scored against reality"],
    ["Latency p50", isNum(ops.latency_ms_p50) ? fx(ops.latency_ms_p50, 0) + " ms" : "—", `p90 ${isNum(ops.latency_ms_p90) ? fx(ops.latency_ms_p90, 0) : "—"} ms`],
    ["Brier skill long", sg(mq.long_bracket?.skill_vs_climatology, 3), "> 0 beats base rates"], ["Brier skill short", sg(mq.short_bracket?.skill_vs_climatology, 3), "> 0 beats base rates"],
    ["AUC target first", `${fx(mq.long_bracket?.auc_target_first, 2)} / ${fx(mq.short_bracket?.auc_target_first, 2)}`, "long / short · 0.50 = random"],
    ["ρ(EV, realised R)", sg(edge.spearman_ev_vs_realised_r, 3), `n = ${edge.n || 0} side-decisions`],
    ["Strict signals R", sg(edge.strict_signals_mean_r, 3), `n = ${edge.strict_signals_n || 0} · win ${pct(edge.strict_signals_win_rate)}`],
    ["Explore signals R", sg(edge.exploration_signals_mean_r, 3), `n = ${edge.exploration_signals_n || 0} · win ${pct(edge.exploration_signals_win_rate)}`],
    ["Testnet net PnL", <span key="p" className={ud(t.net_pnl_usdt)}>{sg(t.net_pnl_usdt)}</span>, `${t.closed} trades · from Bybit closed-PnL`],
  ];
  const qr = (b: any, k = "brier") => b && b.n ? (
    <tr key={b.name}><td className="amb">{b.name}</td><td className="r">{b.n}</td><td className="r">{fx(b[k], 4)}</td><td className="r">{fx(b[k + "_climatology"], 4)}</td><td className="r">{fx(b[k + "_uniform"], 4)}</td>
      <td className={`r ${ud(b.skill_vs_climatology)}`}>{sg(b.skill_vs_climatology, 3)}</td><td className="r">{fx(b.accuracy_argmax, 3)} / {fx(b.accuracy_majority_baseline, 3)}</td></tr>
  ) : null;
  const mr = mq.mean_reversion, pit = mq.return_distribution?.pit_histogram_quintiles;
  const bq = (b: any) => b && b.n ? (
    <tr key={b.name}><td className="amb">{b.name}</td><td className="r">{b.n}</td><td className="r">{fx(b.brier, 4)}</td><td className="r">{fx(b.brier_climatology, 4)}</td><td className="r">—</td>
      <td className={`r ${ud(b.skill_vs_climatology)}`}>{sg(b.skill_vs_climatology, 3)}</td><td className="r">AUC {fx(b.auc, 3)}</td></tr>
  ) : null;
  const vb = m.vs_base_rates || {}, cw = m.calibration_weights || {};
  return (
    <main className="view on">
      {toolbar}
      <div className="strip">{cells.map(([l, n, s]) => <div className="cell" key={l}><div className="l">{l}</div><div className="n">{n}</div><div className="s">{s}</div></div>)}</div>
      <div className="grid-eval">
        <Panel code="FIND" title="Findings" span2><ul className="obs">{(m.observations || []).map((o: string, i: number) => <li key={i}>{o}</li>)}{!m.observations?.length && <li>No findings yet.</li>}</ul></Panel>
        <Panel code="CAL" title="Calibration · P(long target first)"><Reliability cal={mq.long_bracket?.calibration_target_first} horizon={st?.horizon_minutes} /></Panel>
        <Panel code="CAL" title="Calibration · P(short target first)"><Reliability cal={mq.short_bracket?.calibration_target_first} horizon={st?.horizon_minutes} /></Panel>
        <Panel code="EDGE" title="Realised R by predicted EV"><HBars items={edge.by_predicted_ev} vk="mean_realised_r" lk="ev_bucket" unit="R" /><div className="muted">Predicted EV bucket → mean realised bracket R net of costs, over every labelled bar and both sides.</div></Panel>
        <Panel code="PIT" title="Return distribution · PIT histogram">{pit ? <><HBars items={pit.map((n: number, i: number) => ({ q: `Q${i + 1}`, v: n, n }))} vk="v" lk="q" signed={false} /><div className="muted">Flat = calibrated · U-shape = overconfident · hump = underconfident</div></> : <div className="empty">Needs labelled decisions</div>}</Panel>
        <Panel code="SCOR" title="Per-question scores vs baselines" tight>
          <table className="t"><thead><tr><th>Question</th><th className="r">n</th><th className="r">Score</th><th className="r">Climat.</th><th className="r">Uniform</th><th className="r">Skill</th><th className="r">Acc / majority</th></tr></thead>
            <tbody>{qr(mq.long_bracket)}{qr(mq.short_bracket)}{qr(mq.return_distribution, "rps")}{qr(mq.regime)}
              {bq(mq.direction)}{bq(mq.big_move)}
              {mr && <tr><td className="amb">mean_reversion</td><td className="r">{mr.n}</td><td className="r">{fx(mr.brier, 4)}</td><td className="r">{fx(mr.brier_climatology, 4)}</td><td className="r">—</td><td className={`r ${ud(mr.skill_vs_climatology)}`}>{sg(mr.skill_vs_climatology, 3)}</td><td className="r">AUC {fx(mr.auc, 3)}</td></tr>}</tbody></table>
          <div className="muted" style={{ padding: "4px 8px" }}>Multiclass Brier, or RPS for ordered bins. Lower is better. Skill = 1 − score / climatology.</div>
        </Panel>
        <Panel code="BASE" title="Model vs the base rates it was given · calibration weights" tight>
          {Object.keys(vb).length ? (
            <table className="t">
              <thead><tr><th>Question</th><th className="r">n</th><th className="r">Brier model</th><th className="r">Brier base rates</th><th className="r">Skill vs base</th><th className="r">Best w</th><th className="r">w in use</th></tr></thead>
              <tbody>
                {Object.entries<any>(vb).map(([k, r]) => (
                  <tr key={k}><td className="amb">{k}</td><td className="r">{r.n}</td><td className="r">{fx(r.brier_kairos, 4)}</td><td className="r">{fx(r.brier_base_rates, 4)}</td>
                    <td className={`r ${ud(r.kairos_skill_vs_base)}`}>{sg(r.kairos_skill_vs_base, 3)}</td><td className="r">{fx(r.best_w, 2)}</td>
                    <td className="r">{fx(typeof cw.weights?.[k] === "object" ? cw.weights[k].model : cw.weights?.[k], 2)}{cw.report?.[k]?.fitted ? "" : " prior"}</td></tr>
                ))}
              </tbody>
            </table>
          ) : <div className="empty">Needs labelled v2 decisions (each is scored after its horizon)</div>}
          <div className="muted" style={{ padding: "4px 8px" }}>Decisions use w · model + (1 − w) · base rates. w is fitted on independent (non-overlapping) labelled windows once a question has 30; until then it is 0.5. Long and short brackets share one weight. Skill &gt; 0 = the model adds information beyond the base rates. Weights are fitted per model and per trading timeframe. The VALID screen (F6) has intervals, splits and walk-forward results.</div>
        </Panel>
        <Panel code="PRB" title="Consistency probes" tight><KV rows={[
          ["Repeat probes", cons.repeat_probes], ["Identical answers", fx(cons.repeat_identical_share, 3)], ["Mean max |Δp| on repeat", fx(cons.repeat_max_abs_prob_diff_mean, 4)],
          ["Mirror probes", cons.mirror_probes], ["Mirror abs error", fx(cons.mirror_abs_error_mean, 4), "|P long target (orig) − P short target (mirrored)|; 0 = symmetric"],
          ["Mirror lean flips", fx(cons.mirror_lean_flips_sign_share, 3)], ["Coherence mean", fx(cons.coherence_mean, 3)], ["Both sides EV > 0", cons.both_sides_positive_ev], ["Both targets > 50%", cons.both_targets_above_50pct],
        ]} /></Panel>
        <Panel code="OPS" title="Operations" tight><KV rows={[
          ["Errors", Object.entries<any>(ops.errors || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none"], ["Contract issues", ops.responses_with_issues],
          ["Latency p99", isNum(ops.latency_ms_p99) ? fx(ops.latency_ms_p99, 0) + " ms" : "—"], ["Input tokens mean / max", `${fx(ops.input_tokens_mean, 0)} / ${ops.input_tokens_max ?? "—"}`],
          ["Retried", ops.retried_requests], ["Policy actions", Object.entries<any>(m.policy.actions).map(([k, v]) => `${k} ${v}`).join(", ") || "—"],
        ]} /></Panel>
        <Panel code="PNL" title="Testnet trading · Bybit closed-PnL, every fill" tight>{t.closed ? <KV rows={[
          ["Closed trades", t.closed], ["Net PnL (after fees)", <span key="n" className={ud(t.net_pnl_usdt)}>{sg(t.net_pnl_usdt)} USDT</span>], ["Fees", `${fx(t.fees_usdt)} USDT`],
          ["Win rate", fx(t.win_rate, 3)], ["Profit factor", fx(t.profit_factor, 2)], ["Avg realised R", sg(t.avg_r, 3), "PnL ÷ (qty × |entry − live stop|)"], ["Avg predicted EV", sg(t.avg_predicted_ev_r, 3) + "R"],
          ["Max drawdown", `${fx(t.max_drawdown_usdt)} USDT`],
          ["By mode", Object.entries<any>(t.by_mode || {}).map(([k, v]) => `${k} ${v.n} · ${sg(v.pnl)} · win ${(100 * v.win_rate).toFixed(0)}%`).join(" | ") || "—"],
          ["Exit reasons", Object.entries<any>(t.by_reason).map(([k, v]) => `${k} ${v}`).join(", ")],
        ]} /> : <div className="empty">No closed testnet trades yet</div>}</Panel>
        <Panel code="EXEC" title="Execution quality · real testnet fills" tight><KV rows={[
          ["Entry slippage", dist(ex.entry_slip_bp), "Fill vs best testnet price at order time; + = worse"],
          ["Stop exit slippage", dist(ex.stop_exit_slip_bp), "Stop fill vs trigger; + = worse"],
          ["Target exit slippage", dist(ex.target_exit_slip_bp), "Target fill vs trigger; + = worse"],
          ["Entry latency", ex.entry_latency_ms ? `${fx(ex.entry_latency_ms.p50, 0)} ms p50 · p90 ${fx(ex.entry_latency_ms.p90, 0)}` : "—", "Order sent → fill confirmed"],
          ["Fill ratio", isNum(ex.fill_ratio_mean) ? `${fx(100 * ex.fill_ratio_mean, 1)}%` : "—", "Filled ÷ requested (IOC takes only liquidity inside the cap)"],
          ["Multi-fill exits", ex.multi_fill_exits ?? "—"], ["Worst trade", isNum(ex.worst_r) ? sg(ex.worst_r, 2) + "R" : "—"],
          ["Entry attempts", Object.entries<any>(t.attempts_by_status || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "—"],
        ]} /></Panel>
        <Panel code="RAW" title="Raw metrics" span2><details><summary>show JSON</summary><pre className="code">{JSON.stringify(m, null, 1)}</pre></details></Panel>
      </div>
    </main>
  );
}

const pct0 = (x: any) => (isNum(x) ? (100 * Number(x)).toFixed(0) + "%" : "—");
const when = (r: any) => (r.started_ts ? `${dt(r.started_ts)} → ${r.ended_ts ? dt(r.ended_ts) : "now"}` : "—");

function RunsTable({ runs, overall, sel, onSel }: { runs: any[]; overall: any; sel: number | null; onSel: (id: number | null) => void }) {
  const row = (r: any, isAll = false) => {
    const c = r.config_json || {};
    return (
      <tr key={isAll ? "all" : r.id} className={`click ${(isAll ? sel === null : sel === r.id) ? "sel" : ""}`} onClick={() => onSel(isAll ? null : r.id)}>
        <td className="sym">{isAll ? "OVERALL" : `RUN ${r.id}`}{r.current && <> <span className="tag long">LIVE</span></>}</td>
        <td className="wrap" style={{ maxWidth: 280 }}>{isAll ? "all runs combined" : r.name.replace(/^Run \d+ · /, "")}</td>
        <td className="muted">{when(r)}</td>
        <td>{isAll ? "—" : <><b className="amb">{c.model_provider ? String(c.model_provider).toUpperCase() : "KAIROS"}</b> · {c.qset || "—"} · {c.exploration_mode ? <span className="tag xp">EXPLORE</span> : <span className="tag amb">STRICT</span>} · {(c.symbols || []).length} sym</>}</td>
        <td className="r">{r.decisions}</td>
        <td className="r">{r.labelled}</td>
        <td className="r">{r.closed}{r.open_trades ? <span className="muted"> +{r.open_trades} open</span> : ""}</td>
        <td className={`r ${ud(r.net_pnl)}`}>{sg(r.net_pnl)}</td>
        <td className="r">{fx(r.fees)}</td>
        <td className="r">{pct0(r.win_rate)}</td>
        <td className={`r ${ud(r.avg_r)}`}>{sg(r.avg_r, 2)}</td>
        <td className="r">{fx(r.profit_factor, 2)}</td>
        <td className="r">{isNum(r.entry_slip_bp) ? sg(r.entry_slip_bp, 1) : "—"}</td>
        <td className="r">{isNum(r.stop_slip_bp) ? sg(r.stop_slip_bp, 1) : "—"}</td>
        <td className="r" data-tip="Signals scored on mainnet labels (traded or not)">{r.strict_n ? `${r.strict_n} · ${sg(r.strict_r, 2)}R` : "0"} / {r.explore_n ? `${r.explore_n} · ${sg(r.explore_r, 2)}R` : "0"}</td>
        <td className={`r ${ud(r.skill_long)}`}>{sg(r.skill_long, 2)} / {sg(r.skill_short, 2)}</td>
        <td className={`r ${ud(r.vs_base_long)}`}>{sg(r.vs_base_long, 2)}</td>
      </tr>
    );
  };
  return (
    <table className="t">
      <thead><tr><th>Run</th><th>Setup</th><th>Period</th><th>Config</th><th className="r">Model req</th><th className="r">Labelled</th><th className="r">Trades</th>
        <th className="r">Net PnL $</th><th className="r">Fees $</th><th className="r">Win</th><th className="r">Avg R</th><th className="r">PF</th>
        <th className="r">Entry slip</th><th className="r">Stop slip</th><th className="r">Strict / explore signals</th><th className="r">Skill L / S</th><th className="r">vs base</th></tr></thead>
      <tbody>{runs.map((r) => row(r))}{overall && row(overall, true)}</tbody>
    </table>
  );
}

export function useRuns(rev: number) {
  const [data, setData] = useState<{ runs: any[]; overall: any } | null>(null);
  useEffect(() => { api("/api/runs").then(setData).catch(() => {}); }, [rev]);
  return data;
}

export function TradesView() {
  const rev = useStore((s) => s.rev.trades);
  const [run, setRun] = useState<number | null>(null);
  const [ts, setTs] = useState<any[] | null>(null);
  const runs = useRuns(rev);
  useEffect(() => { api<any[]>(run ? `/api/trades?run=${run}` : "/api/trades").then(setTs).catch(() => {}); }, [rev, run]);
  return (
    <main className="view on">
      <section className="pnl" style={{ marginBottom: 4 }}>
        <div className="ph"><span className="code">RUNS</span>Test runs · click a row to filter the trades below<span className="ph-r">PnL from Bybit closed-PnL · signals and skill from mainnet labels</span></div>
        <div className="pb tight scroll">{runs ? <RunsTable runs={runs.runs} overall={runs.overall} sel={run} onSel={setRun} /> : <div className="empty">Loading runs…</div>}</div>
      </section>
      <section className="pnl">
        <div className="ph"><span className="code">TRD</span>Bot trades · {run ? `Run ${run}` : "all runs"} · Bybit testnet<span className="ph-r">P&amp;L, fees and exit price summed from every Bybit closed-PnL row</span></div>
        <div className="pb tight scroll">
          {!ts ? <div className="empty">Loading…</div> : !ts.length ? <div className="empty">No trades yet. They appear once a model key is set and AUTO-TRADE is on.</div> : (
            <table className="t">
              <thead><tr><th>Run</th><th>Opened</th><th>Sym</th><th>Side</th><th>Mode</th><th className="r">Qty (req)</th><th className="r">Ref</th><th className="r">Entry</th><th className="r">Slip bp</th><th className="r">TP</th><th className="r">SL</th>
                <th>Status</th><th>Exit</th><th className="r">Exit px</th><th className="r">Exit slip</th><th className="r">Fills</th><th className="r">PnL</th><th className="r">Fees</th><th className="r">R</th><th className="r">EV R</th><th>Dec</th></tr></thead>
              <tbody>
                {ts.map((t) => {
                  const r = isNum(t.r_mult) ? t.r_mult : isNum(t.realized_pnl) && t.risk_usd ? t.realized_pnl / t.risk_usd : null;
                  const mode = t.mode || t.notes;
                  const partial = t.requested_qty && t.qty < t.requested_qty * (1 - 1e-9);
                  return (
                    <tr key={t.id}>
                      <td className="amb">{t.run_id ?? "—"}</td><td className="muted">{dt(t.opened_ts)}</td><td className="sym">{short(t.symbol)}</td><td><span className={`tag ${t.side}`}>{t.side.toUpperCase()}</span></td>
                      <td>{mode === "exploration" ? <span className="tag xp">EXPLORE</span> : mode === "strict" ? <span className="tag amb">STRICT</span> : ""}</td>
                      <td className="r">{t.qty}{partial && <span className="tag part" data-tip={`IOC filled ${t.qty} of ${t.requested_qty}`}> PART</span>}</td>
                      <td className="r muted">{t.ref_price ?? "—"}</td><td className="r">{t.entry_price ?? "—"}</td><td className={`r ${ud(-(t.entry_slip_bp ?? 0))}`}>{isNum(t.entry_slip_bp) ? sg(t.entry_slip_bp, 1) : "—"}</td>
                      <td className="r">{t.tp_price}</td><td className="r">{t.sl_price}</td>
                      <td>{t.status}</td><td>{t.close_reason || ""}</td><td className="r">{isNum(t.exit_price) ? Number(t.exit_price).toPrecision(8) : "—"}</td>
                      <td className={`r ${ud(-(t.exit_slip_bp ?? 0))}`}>{isNum(t.exit_slip_bp) ? sg(t.exit_slip_bp, 1) : "—"}</td><td className="r">{t.exit_fills ?? "—"}</td>
                      <td className={`r ${ud(t.realized_pnl)}`}>{sg(t.realized_pnl)}</td><td className="r">{fx(t.fees, 3)}</td><td className={`r ${ud(r)}`}>{sg(r)}</td><td className="r">{sg(t.ev_r, 3)}</td>
                      <td>{t.decision_id > 0 && <a href="#" onClick={(e) => { e.preventDefault(); openDecision(t.decision_id); }}>#{t.decision_id}</a>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </main>
  );
}

export function NewsView() {
  const n = useStore((s) => s.news);
  useEffect(() => { loadNews().catch(() => {}); }, []);
  return (
    <main className="view on">
      <section className="pnl" style={{ marginBottom: 4 }}>
        <div className="ph"><span className="code">NRET</span>Kairos news_return forecasts<span className="ph-r">15m horizon · 7 bins scaled by round-trip cost c</span></div>
        <div className="pb tight">
          {n.forecasts.length ? (
            <table className="t">
              <thead><tr><th>Time</th><th>Asset</th><th>Headline</th><th>Distribution −4c … +4c</th><th className="r">Up − down</th><th className="r">Realised 15m</th></tr></thead>
              <tbody>
                {n.forecasts.map((f: any) => {
                  const p = f.probs_json, lean = p ? p.slice(4).reduce((a: number, b: number) => a + b, 0) - p.slice(0, 3).reduce((a: number, b: number) => a + b, 0) : null;
                  return (
                    <tr key={f.id}>
                      <td className="muted">{dt(f.ts)}</td><td className="sym">{short(f.symbol)}</td><td className="wrap">{f.title} <span className="muted">{f.source}</span></td>
                      <td>{p ? <svg width={146} height={20}>{p.map((v: number, i: number) => <rect key={i} x={i * 21} y={20 - v * 20} width={18} height={Math.max(v * 20, 1)} fill={i < 3 ? "var(--down)" : i > 3 ? "var(--up)" : "#6a6a6a"} data-tip={`bin ${i}: ${(100 * v).toFixed(1)}%`} />)}</svg> : <span className="tag err">{f.error_kind || "—"}</span>}</td>
                      <td className={`r ${ud(lean)}`}>{sg(lean, 2)}</td>
                      <td className="r">{f.label_json ? `${sg(f.label_json.return_bp, 1)} bp · bin ${f.label_json.bin}` : <span className="muted">pending</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : <div className="empty">No news forecasts yet. New headlines that mention a traded asset are sent to Kairos news_return (Kairos only; paused while another model is active).</div>}
        </div>
      </section>
      <section className="pnl">
        <div className="ph"><span className="code">TOP</span>Headlines</div>
        <div className="pb tight">
          <table className="t"><tbody>
            {n.headlines.map((h: any) => (
              <tr key={h.id}><td className="muted">{h.published_ts ? dt(h.published_ts) : ""}</td><td className="amb">{h.source}</td>
                <td className="wrap"><a href={h.link} target="_blank" rel="noopener noreferrer" style={{ color: "var(--text)" }}>{h.title}</a> {h.symbols.map((s: string) => <span key={s} className="tag flat">{short(s)}</span>)}{h.market_wide && <> <span className="tag amb">MKT</span></>}</td></tr>
            ))}
          </tbody></table>
        </div>
      </section>
    </main>
  );
}

export function LogView() {
  const rev = useStore((s) => s.rev.events);
  const [ev, setEv] = useState<any[]>([]);
  useEffect(() => {
    const id = setTimeout(() => api<any[]>("/api/events").then(setEv).catch(() => {}), 200);
    return () => clearTimeout(id);
  }, [rev]);
  const lvl = (l: string) => (l === "error" ? "err" : l === "trade" ? "long" : l === "warn" ? "amb" : "flat");
  return (
    <main className="view on">
      <section className="pnl">
        <div className="ph"><span className="code">LOG</span>System events<span className="ph-r">live</span></div>
        <div className="pb tight">
          <table className="t">
            <thead><tr><th>Time</th><th>Level</th><th>Kind</th><th>Sym</th><th>Message</th></tr></thead>
            <tbody>{ev.map((e) => <tr key={e.id}><td className="muted">{dt(e.ts)}</td><td><span className={`tag ${lvl(e.level)}`}>{String(e.level).toUpperCase()}</span></td><td>{e.kind}</td><td className="sym">{e.symbol ? short(e.symbol) : ""}</td><td className="wrap">{e.message}</td></tr>)}</tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
