"use client";
import { useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import { loadOverview, selectTf } from "@/lib/data";
import { setState, useStore, useVenue } from "@/lib/store";
import { age, dt, fx, hrs, sg, short, ud, usd } from "@/lib/format";
import { ModelLogo, PhIcon, Switch, Sym, VenueLogo } from "./ui";
import { ShieldCheck, X } from "lucide-react";

function Dialog({ className, children }: { className?: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current!;
    d.showModal();
    const onClose = () => setState({ modal: null });
    d.addEventListener("close", onClose);
    return () => d.removeEventListener("close", onClose);
  }, []);
  return (
    <dialog ref={ref} className={className} onClick={(e) => e.target === ref.current && ref.current?.close()}>
      <form method="dialog"><button className="iconbtn close" aria-label="Close"><X size={16} strokeWidth={1.7} /></button></form>
      {children}
    </dialog>
  );
}

const J = ({ x }: { x: unknown }) => <pre className="code">{JSON.stringify(x, null, 1) ?? "—"}</pre>;

export function Modals() {
  const modal = useStore((s) => s.modal);
  if (!modal) return null;
  if (modal.kind === "decision") return <DecisionModal d={modal.data} />;
  if (modal.kind === "state") return <StateModal data={modal.data} />;
  if (modal.kind === "keys") return <KeysModal />;
  if (modal.kind === "help") return <HelpModal />;
  if (modal.kind === "tf") return <TfModal tf={modal.data} />;
  if (modal.kind === "universe") return <UniverseModal />;
  if (modal.kind === "strategy") return <StrategyModal />;
  if (modal.kind === "config") return <ConfigModal />;
  return null;
}

function DecisionModal({ d }: { d: any }) {
  return (
    <Dialog>
      <div className="ph"><PhIcon code="DEC" />Decision #{d.id} · {d.symbol} · {dt(d.ts)}</div>
      <p>question set {d.qset_version} · {fx(d.latency_ms, 0)} ms · {d.request_bytes ?? "—"} bytes · attempts {d.attempts ?? "—"} · request_id {d.request_id || "—"}</p>
      {d.issues_json?.length > 0 && <p className="dn">Contract issues: {d.issues_json.join("; ")}</p>}
      <div className="mgrid">
        <div><h3>State sent</h3><J x={d.state_json} /></div>
        <div><h3>Raw answers</h3><J x={d.answers_json} /><h3>Usage · model metadata</h3><J x={{ usage: d.usage_json, meta: d.model_meta_json }} /></div>
        <div><h3>Policy decision</h3><J x={d.decision_json} /><h3>Realised label</h3><J x={d.label_json || "pending"} /></div>
        <div><h3>Questions</h3><J x={d.questions_json} />{d.probes?.length > 0 && <><h3>Probes</h3><J x={d.probes} /></>}</div>
      </div>
    </Dialog>
  );
}

function StateModal({ data }: { data: any }) {
  const p = data.preview, n = JSON.stringify(p.state).length;
  return (
    <Dialog>
      <div className="ph"><PhIcon code="STATE" />Exact state sent to the model · {data.symbol}</div>
      <p>{n} chars ≈ {Math.round(n / 3.6)} tokens. Built {age(Date.now() / 1000 - p.ts)} ago.</p>
      <div><J x={p.state} /></div>
    </Dialog>
  );
}

function HelpModal() {
  const rows: [string, string][] = [
    ["BTC · ETH · SOL …", "Load a symbol into chart, model signal, book and stats"], ["ASK", "Ask the active model now for the loaded symbol (1 request)"],
    ["PRB", "Ask + repeat & mirror consistency probes (4 requests)"], ["1M 5M 15M 1H 4H", "Chart timeframe (view only)"],
    ["POS 10", "Max open positions (1–50); new entries only"], ["TF 1M · TF 5M · TF 15M · TF 1H", "Trading timeframe: how often the model decides and trades"], ["MODEL", "Kairos or Jev: switch with Model under the header (starts a new run)"],
    ["UNIV", "Choose the symbols the bot watches and trades"], ["STRAT", "Strategy: brackets, entries, mirror averaging, triggers, filters, sizing"],
    ["CONFIG · VENUE", "Every other setting: venue (demo / testnet), costs, risk, gates, execution, model budget"],
    ["MON EVAL VALID TRD NEWS LOG", "Switch screen (F1 F2 F6 F3 F4 F5)"], ["KEYS · HELP", "API keys · this list"], ["/", "Focus the symbol / command field from anywhere"],
  ];
  return (
    <Dialog className="keys">
      <div className="ph"><PhIcon code="HELP" />Commands</div>
      <table className="t" style={{ marginTop: 6 }}><tbody>{rows.map(([k, v]) => <tr key={k}><td><span className="kbd">{k}</span></td><td className="wrap">{v}</td></tr>)}</tbody></table>
    </Dialog>
  );
}

function TfModal({ tf }: { tf: string }) {
  const st = useStore((s) => s.ov?.settings);
  if (!st) return null;
  const cur = st.tf_profiles[st.decision_tf], nxt = st.tf_profiles[tf];
  if (!nxt) return null;
  if (tf === st.decision_tf) {
    setTimeout(() => setState({ modal: null, cmdHint: `Already trading every ${nxt.label.toLowerCase()}` }), 0);
    return null;
  }
  const budget = st.kairos_max_requests_per_hour, over = nxt.required_rph > budget;
  const tok = (rph: number) => `~${Math.round((rph * 14800) / 1e5) / 10}M`;
  const Row = ({ k, a, b }: { k: string; a: string; b: string }) => <><div className="h">{k}</div><div className="old">{a}</div><div className="new">{b}</div></>;
  const go = async (raise: boolean) => {
    try {
      await post("/api/decision-tf", { tf, raise_budget: raise });
      setState({ modal: null });
      await loadOverview();
      selectTf(tf);
    } catch (e: any) {
      alert(e.message);
    }
  };
  return (
    <Dialog className="keys">
      <div className="ph"><PhIcon code="TF" />Switch trading timeframe</div>
      <div style={{ margin: "8px 10px" }}>
        <div className="tfgrid">
          <Row k="" a={"Now: " + cur.label.toLowerCase()} b={"New: " + nxt.label.toLowerCase()} />
          <Row k="Model asked" a={`every ${cur.label} close`} b={`every ${nxt.label} close`} />
          <Row k="Max hold / time-stop" a={hrs(cur.horizon_minutes)} b={hrs(nxt.horizon_minutes)} />
          <Row k="Stop sized from" a={`${cur.atr_tf}m ATR × ${st.sl_atr} (≥ ${st.min_sl_bp}bp)`} b={`${nxt.atr_tf}m ATR × ${st.sl_atr} (≥ ${st.min_sl_bp}bp)`} />
          <Row k="Model requests / hour" a={`~${cur.required_rph}`} b={`~${nxt.required_rph}`} />
          <Row k="Input tokens / hour" a={tok(cur.required_rph)} b={tok(nxt.required_rph)} />
        </div>
        <p className="muted" style={{ margin: "8px 0 0" }}>Takes effect at the next {nxt.label} bar close. Open positions keep their original time-stop. Every decision is stamped with its timeframe, so EVAL can compare them separately. Saved to .env.</p>
        {over && <div className="warnbox"><b className="amb">Budget.</b> {nxt.label} needs ~{nxt.required_rph} requests/hour but the cap is {budget}. With the current cap, about {Math.round(100 * (1 - budget / nxt.required_rph))}% of bars would be skipped.</div>}
        {tf === "1" && <div className="warnbox"><b className="amb">1m note.</b> Kairos answers in ~7–13 s (measure Jev in EVAL), so 1m entries land ~10 s into the next bar, and the 40bp stop floor means fees are ~0.37R per trade.</div>}
      </div>
      <div className="keyactions" style={{ margin: "8px 10px" }}>
        {over ? <><button className="btn primary" onClick={() => go(true)}>Switch and raise cap to {nxt.required_rph}/h</button><button className="btn" onClick={() => go(false)}>Switch, keep cap {budget}</button></>
          : <button className="btn primary" onClick={() => go(false)}>Switch to {nxt.label.toLowerCase()}</button>}
        <button className="btn ghost" onClick={() => setState({ modal: null })}>Cancel</button>
      </div>
    </Dialog>
  );
}

type Msg = { ok: boolean; text: string } | null;
/** The service re-executes itself after a venue or universe change: wait for it, then reload. */
function waitForRestart(onFail: (m: string) => void) {
  const t0 = Date.now();
  const wait = async () => {
    try {
      await api("/api/overview");
      location.reload();
    } catch {
      if (Date.now() - t0 < 90_000) setTimeout(wait, 1500);
      else onFail("service did not come back within 90 s");
    }
  };
  setTimeout(wait, 4000);
}

async function switchVenue(target: string, onRestart: () => void, onErr: (m: string) => void) {
  const warn = target === "demo"
    ? "Switch to BYBIT DEMO: orders go to your Demo Trading account (demo money, mainnet market)."
    : "Switch to BYBIT TESTNET: orders go to your testnet account.";
  if (!confirm(`${warn}\n\nThe service restarts (~15 s), a new run starts and AUTO-TRADE switches off.`)) return;
  try {
    await post("/api/config", { venue: target });
    onRestart();
    waitForRestart(onErr);
  } catch (e: any) {
    onErr(e.message);
  }
}

type KeyResult = { ok: boolean; text: string } | null;

/** One provider: logo, name, status and a switch. Switching it on shows the fields to paste keys into. */
function Provider({ logo, name, sub, status, active, defaultOpen, children }: {
  logo: React.ReactNode; name: string; sub: string; status: React.ReactNode; active?: boolean; defaultOpen: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className={`prov${open ? " open" : ""}`}>
      <div className="prov-head">
        <span className="prov-logo" aria-hidden="true">{logo}</span>
        <span className="prov-name"><b>{name}{active && <span className="pill ok" style={{ marginLeft: 8, verticalAlign: 2 }}>in use</span>}</b><span>{sub}</span></span>
        {status}
        <Switch checked={open} onChange={setOpen} label={`Set up ${name}`} tone="neutral" />
      </div>
      {open && <div className="prov-body">{children}</div>}
    </section>
  );
}

const KeyStatus = ({ set, hint, extra, bad }: { set: boolean; hint?: string; extra?: string; bad?: boolean }) =>
  set ? <span className={`pill ${bad ? "bad" : "ok"}`}>saved {hint}{extra ? `, ${extra}` : ""}</span> : <span className="pill">not set</span>;

function ModelKeyCard({ id, name, sub, logo, info, k, active, onSaved, withUrl }: {
  id: "kairos" | "jev"; name: string; sub: string; logo: React.ReactNode; info: React.ReactNode; k: any; active: boolean; onSaved: () => void; withUrl?: boolean;
}) {
  const key = useRef<HTMLInputElement>(null), url = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<KeyResult>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const st = k?.[id];
  const result = (r: any) => setMsg(r.ok ? { ok: true, text: `${name} reachable in ${r.latency_ms} ms, model ${r.model || ""}` } : { ok: false, text: r.error });
  const run = async (what: string, fn: () => Promise<void>) => { setBusy(what); try { await fn(); } finally { setBusy(null); } };
  return (
    <Provider logo={logo} name={name} sub={sub} active={active} defaultOpen={!!k && !st?.set && active}
      status={k && <KeyStatus set={!!st?.set} hint={st?.hint} />}>
      <p>{info}</p>
      <label className="fld">API key<input ref={key} type="password" autoComplete="off" spellCheck={false} placeholder={st?.set ? `saved ${st.hint}, paste a new key to replace it` : `paste your ${name} API key`} /></label>
      {withUrl && <label className="fld">Base URL (optional)<input ref={url} type="text" autoComplete="off" spellCheck={false} placeholder={st?.base_url || "https://minara.cloud"} /></label>}
      <div className="keyactions">
        <button className="btn primary" type="button" disabled={!!busy} onClick={() => run("save", async () => {
          const v = key.current!.value.trim();
          if (!v) return setMsg({ ok: false, text: "Paste a key first." });
          try {
            result(await post(`/api/keys/${id}`, withUrl ? { api_key: v, base_url: url.current?.value.trim() || null } : { api_key: v }));
            key.current!.value = "";
          } catch (e: any) { setMsg({ ok: false, text: e.message }); }
          onSaved();
        })}>{busy === "save" ? "Saving…" : "Save and test"}</button>
        <button className="btn" type="button" disabled={!!busy || !st?.set} onClick={() => run("test", async () => {
          try { result(await post(`/api/keys/${id}/test`)); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
        })}>{busy === "test" ? "Testing…" : "Test saved key"}</button>
        <span className="muted">A test uses 1 request from the hourly budget.</span>
      </div>
      <div className={`keymsg ${msg ? (msg.ok ? "ok" : "bad") : ""}`} role="status">{msg?.text}</div>
    </Provider>
  );
}

function BybitKeyCard({ venue, k, onSaved }: { venue: "demo" | "testnet"; k: any; onSaved: () => void }) {
  const key = useRef<HTMLInputElement>(null), secret = useRef<HTMLInputElement>(null);
  const [msg, setMsg] = useState<KeyResult>(null);
  const [busy, setBusy] = useState(false);
  const kv = k?.bybit_venues?.[venue];
  const active = !!kv?.active;
  const connected = active && !!k?.bybit?.connected;
  const demo = venue === "demo";
  return (
    <Provider logo={<VenueLogo size={24} />} name={`Bybit ${demo ? "Demo" : "Testnet"}`} active={active}
      sub={demo ? "Demo funds on the real mainnet market" : "Bybit's separate test exchange"} defaultOpen={!!k && !kv?.api_key?.set && active}
      status={k && <KeyStatus set={!!kv?.api_key?.set} hint={kv?.api_key?.hint} extra={active ? (connected ? "connected" : "not connected") : undefined} bad={active && !connected && !!kv?.api_key?.set} />}>
      {demo
        ? <p>On <a href={kv?.keys_url || "https://www.bybit.com/app/user/api-management"} target="_blank" rel="noopener noreferrer">bybit.com</a>, switch to <b>Demo Trading</b> first (avatar menu), then open avatar › <b>API</b>. Allow <b>Contract: Orders and Positions</b> and leave <b>withdrawals off</b>. Testnet keys and normal mainnet keys are rejected. Fills, fees and funding are re-booked at real-market cost.</p>
        : <p>Create the key at <a href={kv?.keys_url || "https://testnet.bybit.com/app/user/api-management"} target="_blank" rel="noopener noreferrer">testnet.bybit.com › API management</a>. Allow <b>Contract: Orders and Positions</b> and leave <b>withdrawals off</b>. Mainnet keys are rejected.</p>}
      <label className="fld">API key<input ref={key} type="password" autoComplete="off" spellCheck={false} placeholder={kv?.api_key?.set ? `saved ${kv.api_key.hint}, paste to replace` : `${venue} API key`} /></label>
      <label className="fld">API secret<input ref={secret} type="password" autoComplete="off" spellCheck={false} placeholder={`${venue} API secret`} /></label>
      <div className="keyactions">
        <button className="btn primary" type="button" disabled={busy} onClick={async () => {
          const a = key.current!.value.trim(), b = secret.current!.value.trim();
          if (!a || !b) return setMsg({ ok: false, text: "Both the key and the secret are needed." });
          setBusy(true);
          try {
            const r = await post("/api/keys/bybit", { api_key: a, api_secret: b, venue });
            key.current!.value = secret.current!.value = "";
            setMsg({ ok: true, text: `Saved. ${demo ? "Demo" : "Testnet"} equity ${fx(r.equity)} USDT${r.active ? ". Now trading on this account (watch mode)." : `. Kept for when you switch to ${venue}.`}` });
          } catch (e: any) { setMsg({ ok: false, text: e.message }); }
          setBusy(false);
          onSaved();
        }}>{busy ? "Checking…" : "Verify and save"}</button>
        {!active && <button className="btn" type="button" disabled={busy || !kv?.api_key?.set}
          data-tip={kv?.api_key?.set ? "Restart on this venue (new run, auto-trade off)" : "Save keys for this venue first"}
          onClick={() => switchVenue(venue, () => setMsg({ ok: true, text: `Restarting on ${venue}, reconnecting…` }), (m) => setMsg({ ok: false, text: m }))}>
          Use {demo ? "Demo" : "Testnet"}</button>}
        <span className="muted">Checked against your {venue} wallet before saving.</span>
      </div>
      <div className={`keymsg ${msg ? (msg.ok ? "ok" : "bad") : ""}`} role="status">{msg?.text || (active && k?.bybit?.error) || ""}</div>
    </Provider>
  );
}

function KeysModal() {
  const [k, setK] = useState<any>(null);
  const active = useStore((s) => s.ov?.settings.model_provider) || "kairos";
  const load = () => api("/api/keys").then(setK);
  useEffect(() => { load().catch(() => {}); }, []);
  const saved = () => { load().catch(() => {}); loadOverview(); };
  return (
    <Dialog className="keys">
      <div className="ph"><PhIcon code="KEYS" />API keys</div>
      <div className="keys-intro">
        <ShieldCheck size={18} strokeWidth={1.6} style={{ flex: "none", marginTop: 2, color: "var(--up)" }} aria-hidden="true" />
        <p>Keys are saved {k?.storage === "app" ? "in this app's private data folder (File › Open Data Folder)" : "to .env in the project folder (gitignored)"} with owner-only permissions and apply at once. They are never logged or shown again; only their last four characters appear here. Switch a provider on to add or replace its key.</p>
      </div>
      {!k ? <div className="empty">Loading…</div> : <>
        <ModelKeyCard id="kairos" name="Kairos" sub="DMind via Minara Cloud" logo={<ModelLogo provider="kairos" size={26} />} k={k} active={active === "kairos"} onSaved={saved} withUrl
          info={<>Create a key at <a href="https://minara.cloud" target="_blank" rel="noopener noreferrer">minara.cloud</a> in a <b>Kairos-enabled group</b>.</>} />
        <ModelKeyCard id="jev" name="Jev" sub={`TypeSafe, ${k.jev?.model_id || "jev"}`} logo={<ModelLogo provider="jev" size={26} />} k={k} active={active === "jev"} onSaved={saved}
          info={<>Create a key in the <a href="https://console.typesafe.ai/keys" target="_blank" rel="noopener noreferrer">TypeSafe console</a>. Billed per input token. The model version is pinned (override with <code>CTB_JEV_MODEL</code>) so results never mix releases. Switch to Jev with Model in the bar under the header.</>} />
        <BybitKeyCard venue="demo" k={k} onSaved={saved} />
        <BybitKeyCard venue="testnet" k={k} onSaved={saved} />
      </>}
    </Dialog>
  );
}

/* ---------------------------------------------------------------- symbol universe */
function UniverseModal() {
  const venue = useVenue();
  const [u, setU] = useState<any>(null);
  const [sel, setSel] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restarting, setRestarting] = useState(false);
  useEffect(() => {
    api("/api/universe").then((x) => { setU(x); setSel(x.selected); }).catch((e) => setErr(e.message));
  }, []);
  if (restarting) return <div className="restarting">Restarting with {sel.length} markets, reconnecting…</div>;
  const toggle = (sym: string) => setSel((cur) => (cur.includes(sym) ? cur.filter((x) => x !== sym) : cur.length >= (u?.max_symbols ?? 30) ? cur : [...cur, sym]));
  const need = u ? Math.round(sel.length * u.rph_per_symbol) : 0;
  const over = u && need > u.budget_per_hour;
  const added: string[] = u ? sel.filter((x) => !u.selected.includes(x)) : [];
  const dropped: string[] = u ? u.selected.filter((x: string) => !sel.includes(x)) : [];
  const changed = added.length + dropped.length > 0;
  const typed = q.trim().toUpperCase();
  const typedSym = typed && !typed.endsWith("USDT") ? typed + "USDT" : typed;
  const rows = (u?.candidates || []).filter((c: any) => !typed || c.symbol.includes(typed));
  const apply = async (raise: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      await post("/api/symbols", { symbols: sel, raise_budget: raise });
      setRestarting(true);
      // the service re-executes itself; wait for it to come back, then reload so every panel rebinds
      waitForRestart(setErr);
    } catch (e: any) {
      setErr(e.message);
      setBusy(false);
    }
  };
  return (
    <Dialog>
      <div className="ph"><PhIcon code="UNIV" />Markets to trade<span className="ph-r">signals from Bybit mainnet, orders on Bybit {venue.demo ? "Demo" : "Testnet"}</span></div>
      <p>Pick the perps the bot watches and trades. Each needs ~{u ? fx(u.rph_per_symbol, 1) : "—"} model requests/hour at the current {u?.decision_tf}m trading timeframe (one per bar close, plus consistency probes). Only perps listed on both mainnet and {venue.name} can be chosen. Entries take only {venue.demo ? "mainnet" : "testnet"} liquidity within {u?.max_entry_slip_bp}bp of the best price, so a thin book means small or unfilled orders.</p>
      <div className="unibar">
        <input type="text" placeholder="filter or type a symbol" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="pill">{sel.length} of {u?.max_symbols ?? 30} selected</span>
        <span className={`pill${over ? " bad" : ""}`}>~{need} of {u?.budget_per_hour ?? "—"} model requests/h</span>
        {changed && <span className="muted">+{added.map(short).join(" +") || "0"} · −{dropped.map(short).join(" −") || "0"}</span>}
        <span style={{ marginLeft: "auto" }} />
        <button className="btn sm" onClick={() => u && setSel(u.selected)}>Reset</button>
      </div>
      {typedSym && u && !u.candidates.some((c: any) => c.symbol === typedSym) && (
        <div className="unibar">
          <span className="muted">{typedSym} is not in the top {u.candidates.length} by turnover.</span>
          <button className="btn sm" onClick={() => !sel.includes(typedSym) && setSel([...sel, typedSym])}>Add {typedSym} (checked on apply)</button>
        </div>
      )}
      {!u ? <div className="empty">{err || `Loading the universe and ${venue.name} books…`}</div> : (
        <div className="unigrid">
          <table className="t">
            <thead><tr><th /><th>Symbol</th><th className="r">24h turnover $</th><th className="r">24h</th><th>{venue.demo ? "Demo" : "Testnet"}</th><th className="r">{venue.demo ? "Mainnet" : "Testnet"} bid / ask ≤20bp $</th><th className="r">Spread bp</th><th>Note</th></tr></thead>
            <tbody>
              {rows.map((c: any) => {
                const on = sel.includes(c.symbol), b = c.testnet_book;
                const can = c.mainnet && c.testnet;
                return (
                  <tr key={c.symbol} className={`click ${on ? "on sel" : ""}`} onClick={() => can && toggle(c.symbol)} style={can ? undefined : { opacity: 0.45, cursor: "not-allowed" }}>
                    <td><input type="checkbox" readOnly checked={on} disabled={!can} /></td>
                    <td className="sym"><Sym symbol={c.symbol} /></td>
                    <td className="r">{usd(c.turnover24h)}</td>
                    <td className={`r ${ud(c.pct24h)}`}>{sg(c.pct24h, 2, "%")}</td>
                    <td>{c.testnet ? <span className="tag long">listed</span> : <span className="tag err">not listed</span>}</td>
                    <td className="r">{b?.ok ? `${usd(b.bid_usd_20bp)} / ${usd(b.ask_usd_20bp)}` : b ? <span className="muted">{b.reason}</span> : "—"}</td>
                    <td className="r">{b?.ok ? fx(b.spread_bp, 2) : "—"}</td>
                    <td>{c.asset_type !== "crypto" && <span className="tag amb" data-tip={`Bybit lists this as a ${c.asset_type} perp: it tracks a market with trading hours, so nights and weekends behave unlike crypto and the base rates mix open and closed sessions.`}>{String(c.asset_type).toUpperCase()}</span>} {b?.ok && b.thin ? <span className="tag part" data-tip={`Less than one max-size order ($${usd(u.thin_depth_usd)}) rests within 20bp on one side: entries will be partly filled`}>THIN BOOK</span> : ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {over && <div className="warnbox" style={{ margin: "6px 10px" }}><b className="amb">Budget.</b> {sel.length} symbols need ~{need} model requests/hour, above the cap of {u.budget_per_hour}. Remove symbols, switch to a slower trading timeframe, or raise the cap.</div>}
      {err && <div className="keymsg bad" style={{ margin: "6px 10px" }}>{err}</div>}
      <div className="keyactions" style={{ margin: "8px 10px" }}>
        {over
          ? <button className="btn primary" disabled={busy || !changed} onClick={() => apply(true)}>Apply and raise cap to {need}/h</button>
          : <button className="btn primary" disabled={busy || !changed || !sel.length} onClick={() => apply(false)}>Apply {sel.length} markets</button>}
        <button className="btn ghost" onClick={() => setState({ modal: null })}>Cancel</button>
        <span className="muted">Applying saves to .env and restarts the service (~15 s). Open positions keep being managed; a symbol with an open bot trade cannot be removed. Auto-trade stays as it is.</span>
      </div>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- strategy */
type Field = { k: string; label: string; help: string; kind: "choice" | "bool" | "num" | "list"; choices?: string[]; step?: number };
const FIELDS: Field[] = [
  { k: "bracket_mode", label: "Bracket", kind: "choice", choices: ["vol", "atr"], help: "vol: stop = SL σ × GARCH volatility over the horizon, never so tight that costs exceed MAX COST of it. atr: the original 1.5 × ATR stop with a 40bp floor." },
  { k: "sl_sigma", label: "Stop (σ over horizon)", kind: "num", step: 0.1, help: "Stop distance in standard deviations of the forecast move over the holding time." },
  { k: "reward_risk", label: "Reward : risk", kind: "num", step: 0.1, help: "Target = stop × this." },
  { k: "max_cost_r", label: "Max cost (share of stop)", kind: "num", step: 0.05, help: "Expected execution cost may be at most this share of the stop. 0.2 = fees and slippage use ≤ 20% of 1R." },
  { k: "auto_horizon", label: "Extend horizon", kind: "bool", help: "When the cost floor makes the stop wider than the volatility stop, hold longer so the target stays reachable." },
  { k: "entry_mode", label: "Entries", kind: "choice", choices: ["maker", "taker"], help: "maker: post-only limit at the best bid/ask (maker fee, no slippage), cancelled after the wait. taker: capped IOC." },
  { k: "maker_wait_s", label: "Maker wait (s)", kind: "num", step: 5, help: "How long a maker entry rests before it is cancelled." },
  { k: "symmetrize", label: "Mirror averaging", kind: "bool", help: "Ask every decision on the real and the mirrored market and average: cancels any built-in long/short bias. Doubles requests." },
  { k: "trade_trigger", label: "Trade trigger", kind: "choice", choices: ["events", "every_bar"], help: "events: trade only on bars with a market event (volume spike, range break, VWAP cross, flow burst, liquidation burst…). Decisions are still scored every bar." },
  { k: "direction_source", label: "Direction from", kind: "choice", choices: ["model", "flow", "agree"], help: "model: the calibrated EV picks the side. flow: order flow picks it. agree: trade only when both agree." },
  { k: "require_big_move", label: "Need a likely move", kind: "bool", help: "Trade only when the calibrated P(move ≥ 1 stop) is above its base rate." },
  { k: "require_validated", label: "Need validated skill", kind: "bool", help: "Strict trades only on symbols whose bracket skill interval is above 0 (VALID screen)." },
  { k: "drift_action", label: "On drift", kind: "choice", choices: ["base", "pause", "alert"], help: "base: drop the model's weight to 0 for that question. pause: stop trading. alert: log only." },
  { k: "dynamic_sizing", label: "Dynamic sizing", kind: "bool", help: "Scale risk by edge, volatility regime, run drawdown and correlation with open same-side positions." },
  { k: "be_trigger_r", label: "Break-even at (R)", kind: "num", step: 0.25, help: "Move the stop to break-even once the trade is this many R in profit on mainnet. 0 = off." },
  { k: "max_spread_bp", label: "Max mainnet spread (bp)", kind: "num", step: 1, help: "Liquidity filter." },
  { k: "min_depth_mult", label: "Min depth × our size", kind: "num", step: 1, help: "Mainnet depth within 10bp must be at least this multiple of our notional." },
  { k: "allowed_regimes", label: "Allowed regimes", kind: "list", help: "Comma list, e.g. trend_up/normal_vol,range/low_vol. Empty = all." },
  { k: "ensemble", label: "Extra models", kind: "list", help: "Comma list of other models asked on the real state and blended, e.g. kairos. Weights are fitted per source." },
];

function StrategyModal() {
  const [cur, setCur] = useState<any>(null);
  const [draft, setDraft] = useState<any>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [costs, setCosts] = useState<any>(null);
  useEffect(() => {
    api("/api/strategy").then((r: any) => { setCur(r.strategy); setDraft(r.strategy); setCosts(r.costs); }).catch((e) => setMsg(e.message));
  }, []);
  if (!cur) return <Dialog className="keys"><div className="ph"><PhIcon code="STRAT" />Strategy</div><p>{msg || "Loading…"}</p></Dialog>;
  const changed = FIELDS.filter((f) => JSON.stringify(draft[f.k]) !== JSON.stringify(cur[f.k]));
  const apply = async () => {
    const body: any = {};
    changed.forEach((f) => (body[f.k] = draft[f.k]));
    try {
      const r: any = await post("/api/strategy", body);
      setMsg(r.unchanged ? "Nothing changed." : `Saved. Started ${r.run?.name}.`);
      setCur(r.strategy); setDraft(r.strategy);
      loadOverview();
    } catch (e: any) {
      setMsg(e.message);
    }
  };
  const input = (f: Field) => {
    const v = draft[f.k];
    if (f.kind === "bool") return <Switch checked={!!v} onChange={(x) => setDraft({ ...draft, [f.k]: x })} label={f.label} tone="ok" />;
    if (f.kind === "choice") return <span className="seg">{f.choices!.map((c) => <button key={c} type="button" className={v === c ? "on" : ""} aria-pressed={v === c} onClick={() => setDraft({ ...draft, [f.k]: c })}>{c.replace("_", " ")}</button>)}</span>;
    if (f.kind === "list") return <input value={(v || []).join(",")} onChange={(e) => setDraft({ ...draft, [f.k]: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />;
    return <input type="number" step={f.step} value={v} onChange={(e) => setDraft({ ...draft, [f.k]: Number(e.target.value) })} />;
  };
  return (
    <Dialog className="keys strat">
      <div className="ph"><PhIcon code="STRAT" />Strategy<span className="ph-r">applies while flat and starts a new run</span></div>
      <table className="t" style={{ marginTop: 6 }}>
        <tbody>{FIELDS.map((f) => (
          <tr key={f.k} className={changed.includes(f) ? "sel" : ""}><td className="amb">{f.label}</td><td>{input(f)}</td><td className="wrap muted">{f.help}</td></tr>
        ))}</tbody>
      </table>
      {costs && <p className="muted" style={{ margin: "8px 10px 0" }}>Cost per outcome now ({costs.entry_mode} entries): target {fx(costs.target_bp, 1)}bp · stop {fx(costs.stop_bp, 1)}bp · time exit {fx(costs.time_bp, 1)}bp. Slippage is {costs.source?.stop_slip === "measured" ? "measured from recent trades" : "a default until enough trades are measured"}.</p>}
      {msg && <div className="warnbox">{msg}</div>}
      <div className="keyactions" style={{ margin: "8px 10px" }}>
        <button className="btn primary" disabled={!changed.length} onClick={apply}>Apply{changed.length ? ` ${changed.length} change${changed.length > 1 ? "s" : ""}` : ""}</button>
        <button className="btn" onClick={() => setDraft(cur)}>Reset</button>
        <button className="btn ghost" onClick={() => setState({ modal: null })}>Close</button>
      </div>
    </Dialog>
  );
}

/* ---------------------------------------------------------------- config (every other setting) */
type CfgField = { k: string; env: string; kind: "float" | "int" | "bool" | "choice"; min: number | null; max: number | null;
  group: string; label: string; help: string; choices: string[] | null; restart: boolean };

function ConfigModal() {
  const [meta, setMeta] = useState<CfgField[] | null>(null);
  const [cur, setCur] = useState<Record<string, any>>({});
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState<Msg>(null);
  const [q, setQ] = useState("");
  const [restarting, setRestarting] = useState(false);
  const [keys, setKeys] = useState<any>(null);
  const load = () => api("/api/config").then((r: any) => { setMeta(r.fields); setCur(r.values); setDraft(r.values); });
  useEffect(() => {
    load().catch((e) => setMsg({ ok: false, text: e.message }));
    api("/api/keys").then(setKeys).catch(() => {});
  }, []);
  if (restarting) return <Dialog className="keys"><div className="restarting">Restarting on the new venue, reconnecting…</div>{msg && <div className="warnbox">{msg.text}</div>}</Dialog>;
  if (!meta) return <Dialog className="keys"><div className="ph"><PhIcon code="CONFIG" />Settings</div><p>{msg?.text || "Loading…"}</p></Dialog>;
  const changed = meta.filter((f) => draft[f.k] !== cur[f.k]);
  const venueChange = changed.find((f) => f.restart);
  const shown = meta.filter((f) => !q || `${f.label} ${f.group} ${f.env} ${f.help}`.toLowerCase().includes(q.toLowerCase()));
  const groups = [...new Set(shown.map((f) => f.group))];
  const apply = async () => {
    const body: Record<string, any> = {};
    changed.forEach((f) => (body[f.k] = draft[f.k]));
    if (venueChange && !confirm(`Switch venue to ${String(draft.venue).toUpperCase()}? The service restarts (~15 s), a new run starts and AUTO-TRADE switches off.`)) return;
    try {
      const r: any = await post("/api/config", body);
      if (r.restarting) {
        setRestarting(true);
        waitForRestart((m) => setMsg({ ok: false, text: m }));
        return;
      }
      setCur(r.values); setDraft(r.values);
      setMsg({ ok: true, text: r.unchanged ? "Nothing changed." : `Saved ${changed.length} setting${changed.length > 1 ? "s" : ""}. Applies to new entries and the next decision.` });
      loadOverview();
    } catch (e: any) {
      setMsg({ ok: false, text: e.message });
    }
  };
  const input = (f: CfgField) => {
    const v = draft[f.k];
    if (f.kind === "bool") return <Switch checked={!!v} onChange={(x) => setDraft({ ...draft, [f.k]: x })} label={f.label} tone="ok" />;
    if (f.kind === "choice") return (
      <span className="seg">{f.choices!.map((c) => {
        const noKeys = f.k === "venue" && c !== cur.venue && keys && !keys.bybit_venues?.[c]?.api_key?.set;
        return <button key={c} type="button" className={v === c ? "on" : ""} disabled={!!noKeys}
          data-tip={noKeys ? `No ${c} keys saved yet: add them in KEYS first` : undefined}
          onClick={() => setDraft({ ...draft, [f.k]: c })}>{c}</button>;
      })}</span>
    );
    return <input type="number" step={f.kind === "int" ? 1 : "any"} min={f.min ?? undefined} max={f.max ?? undefined} value={v ?? ""}
      onChange={(e) => setDraft({ ...draft, [f.k]: e.target.value === "" ? "" : Number(e.target.value) })} />;
  };
  return (
    <Dialog className="keys strat">
      <div className="ph"><PhIcon code="CONFIG" />Settings<span className="ph-r">applied at once; a venue change restarts the engine</span></div>
      <div className="unibar">
        <input type="text" placeholder="filter settings" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="muted">Brackets, entries and triggers are under Strategy, symbols under Markets › Edit list, keys under Keys.</span>
      </div>
      <table className="t" style={{ marginTop: 6 }}>
        {groups.map((g) => (
          <tbody key={g}>
            <tr className="cfg-group"><td colSpan={3}>{g}</td></tr>
            {shown.filter((f) => f.group === g).map((f) => (
              <tr key={f.k} className={changed.includes(f) ? "sel" : ""}>
                <td className="amb" data-tip={f.env}>{f.label}</td><td>{input(f)}</td>
                <td className="wrap muted">{f.help}{f.min !== null && f.max !== null ? ` (${f.min}–${f.max})` : ""}</td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
      {msg && <div className={msg.ok ? "keymsg ok" : "warnbox"}>{msg.text}</div>}
      <div className="keyactions" style={{ margin: "8px 10px" }}>
        <button className="btn primary" disabled={!changed.length} onClick={apply}>{venueChange ? "Apply and restart" : "Apply"}{changed.length ? ` ${changed.length} change${changed.length > 1 ? "s" : ""}` : ""}</button>
        <button className="btn" onClick={() => setDraft(cur)}>Reset</button>
        <button className="btn ghost" onClick={() => setState({ modal: null })}>Close</button>
      </div>
    </Dialog>
  );
}
