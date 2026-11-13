"""Validation: is there an edge, where does it live, does it survive costs, and is it still there?

All model-quality numbers here use INDEPENDENT windows only (per symbol, a decision counts only if it
starts after the previous counted decision's horizon has ended). On 1m bars with a 30 min horizon,
consecutive decisions share 29 of 30 minutes of outcome, so the raw label count overstates the
evidence by roughly the horizon length.

- per_symbol: Brier skill against the base rates per question group, with a bootstrap 90% interval
  and a verdict (skill / no skill / undecided). The live policy uses it as a validation gate.
- by_hour, by_session, by_regime, by_event: the same split by when and in what market a decision
  was made, plus the hypothetical result of the model's preferred side and real trade P&L.
- drift: Page-Hinkley test on d_t = Brier(model) - Brier(base) in time order; it alarms when the
  model's error rises persistently above its earlier level.
- walk_forward: chronological folds; weights are fitted on everything before a fold and scored on
  the fold (expanding window), so every reported number is out of sample.
- edge_vs_cost: gross edge of the model's side in R and bp, the execution cost charged, the net, and
  the cost at which the edge would break even.
- execution: implementation shortfall against the mainnet decision price, slippage, fees, market
  impact proxy, markouts after entry, and maker fill statistics.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Any, Callable

from ..calibration import GROUPS, MIN_FIT, fit, independent, rows_for
from ..costs import bracket_ev, outcome_cost_bp
from ..db import DB
from ..ensemble import blend_sources, norm_weights, source_probs
from ..features import session_of

BOOT = 400
CI = (0.05, 0.95)
PH_DELTA = 0.005
PH_LAMBDA = 1.5
MIN_GROUP = 10


def _brier(vec: list[float], k: int) -> float:
    return sum((x - (1.0 if i == k else 0.0)) ** 2 for i, x in enumerate(vec))


def load(db: DB, *, run_id: int | None = None, model: str | None = None, decision_tf: str | None = None,
         symbol: str | None = None, since_ts: float | None = None) -> list[dict[str, Any]]:
    where, params = ["probe IS NULL", "error_kind IS NULL", "parsed_json IS NOT NULL"], []
    for col, val in (("run_id", run_id), ("model_provider", model), ("decision_tf", decision_tf), ("symbol", symbol)):
        if val:
            where.append(f"{col}=?")
            params.append(val)
    if since_ts:
        where.append("ts>=?")
        params.append(since_ts)
    return db.query(f"SELECT id, ts, symbol, horizon_min, sl_bp, tp_bp, parsed_json, label_json, decision_json, action "
                    f"FROM decisions WHERE {' AND '.join(where)} ORDER BY ts", params)


def labelled(decs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in decs if d.get("label_json") and not d["label_json"].get("unlabelable")]


def pair_scores(question_group: str, decs: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """(Brier of the model, Brier of the base rates) per decision and question in the group."""
    out = []
    for q in GROUPS[question_group]:
        for vecs, base, y in rows_for(q, decs):
            out.append((_brier(vecs[0], y), _brier(base, y)))
    return out


def skill(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    bb = sum(b for _, b in pairs)
    return 1 - sum(m for m, _ in pairs) / bb if bb else None


def bootstrap_ci(pairs: list[tuple[float, float]], seed: int = 7) -> tuple[float | None, float | None]:
    if len(pairs) < MIN_GROUP:
        return None, None
    rng = random.Random(seed)
    n = len(pairs)
    sk = []
    for _ in range(BOOT):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        v = skill(sample)
        if v is not None:
            sk.append(v)
    sk.sort()
    return round(sk[int(CI[0] * len(sk))], 4), round(sk[min(len(sk) - 1, int(CI[1] * len(sk)))], 4)


def skill_block(group: str, decs: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = pair_scores(group, decs)
    sk = skill(pairs)
    lo, hi = bootstrap_ci(pairs)
    windows = len(pairs) // len(GROUPS[group]) if pairs else 0
    if lo is None or windows < MIN_FIT:
        verdict = "not enough data"
    elif lo > 0:
        verdict = "skill"
    elif hi < 0:
        verdict = "no skill"
    else:
        verdict = "undecided"
    return {"independent_windows": windows, "skill": round(sk, 4) if sk is not None else None, "ci90": [lo, hi], "verdict": verdict,
            "brier_model": round(statistics.fmean(m for m, _ in pairs), 4) if pairs else None,
            "brier_base": round(statistics.fmean(b for _, b in pairs), 4) if pairs else None}


def model_side_r(d: dict[str, Any]) -> float | None:
    """Realised R (net of the labeller's costs) of the side the calibrated EV preferred."""
    dj, lab = d.get("decision_json") or {}, d.get("label_json") or {}
    side = dj.get("model_side")
    if side is None and dj.get("ev_long_r") is not None and dj.get("ev_short_r") is not None:
        side = "long" if dj["ev_long_r"] >= dj["ev_short_r"] else "short"
    return lab.get(f"{side}_r") if side else None


def group_table(decs: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any], trades_by_dec: dict[int, dict[str, Any]],
                order: list[Any] | None = None) -> list[dict[str, Any]]:
    buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for d in decs:
        k = key(d)
        for kk in (k if isinstance(k, list) else [k]):
            buckets[kk].append(d)
    rows = []
    for k in (order or sorted(buckets, key=str)):
        ds = buckets.get(k, [])
        if not ds:
            continue
        pairs = pair_scores("bracket", ds)
        rs = [r for r in (model_side_r(d) for d in ds) if r is not None]
        tr = [trades_by_dec[d["id"]] for d in ds if d["id"] in trades_by_dec]
        closed = [t for t in tr if t.get("status") == "closed"]
        rows.append({
            "key": k, "independent_windows": len(ds), "bracket_skill": round(skill(pairs), 4) if pairs and skill(pairs) is not None else None,
            "model_side_mean_r": round(statistics.fmean(rs), 4) if rs else None,
            "trades": len(closed), "trade_pnl": round(sum(t["realized_pnl"] or 0 for t in closed), 2),
            "trade_win_rate": round(sum(1 for t in closed if (t["realized_pnl"] or 0) > 0) / len(closed), 3) if closed else None,
            "trade_avg_r": round(statistics.fmean(t["r_mult"] for t in closed if t.get("r_mult") is not None), 3)
                           if any(t.get("r_mult") is not None for t in closed) else None,
        })
    return rows


def page_hinkley(xs: list[float], delta: float = PH_DELTA, lam: float = PH_LAMBDA) -> dict[str, Any]:
    """Detect a persistent increase in the mean of xs (here: model Brier minus base Brier)."""
    mean = 0.0
    m = mn = 0.0
    alarm_at = None
    ph = 0.0
    for t, x in enumerate(xs, 1):
        mean += (x - mean) / t
        m += x - mean - delta
        mn = min(mn, m)
        ph = m - mn
        if ph > lam and alarm_at is None:
            alarm_at = t
    return {"ph": round(ph, 4), "threshold": lam, "alarm": alarm_at is not None, "alarm_at_window": alarm_at, "n": len(xs)}


def drift(decs: list[dict[str, Any]], window: int = 30) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in ("bracket", "direction", "big_move"):
        pairs = []
        for d in decs:  # chronological
            p = pair_scores(group, [d])
            if p:
                pairs.append((statistics.fmean(m for m, _ in p), statistics.fmean(b for _, b in p)))
        diffs = [m - b for m, b in pairs]
        res = page_hinkley(diffs)
        series = []
        for i in range(window, len(pairs) + 1, max(1, window // 3)):
            v = skill(pairs[i - window:i])
            series.append(round(v, 4) if v is not None else None)
        res["rolling_skill"] = series
        res["recent_skill"] = series[-1] if series else None
        out[group] = res
    out["alarm"] = any(out[g]["alarm"] for g in ("bracket", "direction", "big_move"))
    out["questions"] = [q for g in ("bracket", "direction", "big_move") if out[g]["alarm"] for q in GROUPS[g]]
    return out


def walk_forward(decs: list[dict[str, Any]], *, folds: int = 4, min_edge_r: float = 0.15) -> dict[str, Any]:
    """Expanding-window walk-forward on independent labelled decisions (chronological)."""
    n = len(decs)
    if n < (folds + 1) * 8:
        return {"folds": [], "note": f"need at least {(folds + 1) * 8} independent labelled windows, have {n}"}
    size = n // (folds + 1)
    out_folds, all_trades, all_pairs = [], [], []
    for k in range(1, folds + 1):
        train, test = decs[: k * size], decs[k * size: (k + 1) * size if k < folds else n]
        res = fit([r for q in GROUPS["bracket"] for r in rows_for(q, train)])
        w = norm_weights(res["best_weights"])
        pairs_blend, trades, base_trades = [], [], []
        for d in test:
            dj = d.get("decision_json") or {}
            meta = dj.get("meta") or {}
            base = meta.get("base_rates")
            lab = d["label_json"]
            parsed = d.get("parsed_json") or {}
            if not base or "long_bracket" not in parsed:
                continue
            costs = meta.get("costs") or meta.get("cost_bp", 15.0)
            rr, sl = d["tp_bp"] / d["sl_bp"], d["sl_bp"]
            evs, evs_base = {}, {}
            for side in ("long", "short"):
                q = f"{side}_bracket"
                p = blend_sources(source_probs(q, {"model": parsed}), base[q], w)
                evs[side] = bracket_ev(p["target_first"], p["stop_first"], rr, sl, costs)
                evs_base[side] = bracket_ev(base[q]["target_first"], base[q]["stop_first"], rr, sl, costs)
                y = ["target_first", "stop_first", "neither"].index(lab[side]["outcome"])
                pairs_blend.append((_brier([p[i] for i in ("target_first", "stop_first", "neither")], y),
                                    _brier([base[q][i] for i in ("target_first", "stop_first", "neither")], y)))
            best = max(evs, key=evs.get)
            if evs[best] >= min_edge_r:
                trades.append(lab[f"{best}_r"])
            bb = max(evs_base, key=evs_base.get)
            if evs_base[bb] >= min_edge_r:
                base_trades.append(lab[f"{bb}_r"])
        all_trades += trades
        all_pairs += pairs_blend
        out_folds.append({
            "fold": k, "train_windows": len(train), "test_windows": len(test), "weight_model": w.get("model"),
            "oos_bracket_skill": round(skill(pairs_blend), 4) if pairs_blend and skill(pairs_blend) is not None else None,
            "policy_trades": len(trades), "policy_mean_r": round(statistics.fmean(trades), 4) if trades else None,
            "base_only_trades": len(base_trades), "base_only_mean_r": round(statistics.fmean(base_trades), 4) if base_trades else None,
        })
    sk = skill(all_pairs)
    return {"folds": out_folds, "oos_bracket_skill": round(sk, 4) if sk is not None else None,
            "oos_policy_trades": len(all_trades), "oos_policy_mean_r": round(statistics.fmean(all_trades), 4) if all_trades else None,
            "oos_policy_total_r": round(sum(all_trades), 3) if all_trades else 0.0}


def _gross(lab: dict[str, Any], side: str, rr: float, sl_bp: float) -> float:
    o = lab[side]["outcome"]
    if o == "target_first":
        return rr
    if o == "stop_first":
        return -1.0
    return (1 if side == "long" else -1) * lab["return_pct"] * 100 / sl_bp


def edge_vs_cost(decs: list[dict[str, Any]], traded_ids: set[int]) -> dict[str, Any]:
    sets = {"model_side_all_bars": [], "policy_signals": [], "traded": []}
    for d in decs:
        dj, lab = d.get("decision_json") or {}, d["label_json"]
        side = dj.get("model_side") or (("long" if dj["ev_long_r"] >= dj["ev_short_r"] else "short") if dj.get("ev_long_r") is not None else None)
        if not side or side not in lab:
            continue
        rr, sl = d["tp_bp"] / d["sl_bp"], d["sl_bp"]
        costs = (dj.get("meta") or {}).get("costs") or (dj.get("meta") or {}).get("cost_bp", 15.0)
        g = _gross(lab, side, rr, sl)
        c = outcome_cost_bp(lab[side]["outcome"], costs) / sl
        row = (g, c, sl)
        sets["model_side_all_bars"].append(row)
        if d.get("action") in ("long", "short"):
            sets["policy_signals"].append(row)
        if d["id"] in traded_ids:
            sets["traded"].append(row)
    out = {}
    for k, rows in sets.items():
        if not rows:
            out[k] = {"n": 0}
            continue
        g = statistics.fmean(r[0] for r in rows)
        c = statistics.fmean(r[1] for r in rows)
        gbp = statistics.fmean(r[0] * r[2] for r in rows)
        cbp = statistics.fmean(r[1] * r[2] for r in rows)
        out[k] = {"n": len(rows), "gross_edge_r": round(g, 4), "cost_r": round(c, 4), "net_r": round(g - c, 4),
                  "gross_edge_bp": round(gbp, 2), "cost_bp": round(cbp, 2), "net_bp": round(gbp - cbp, 2),
                  "break_even_cost_bp": round(max(0.0, gbp), 2)}
    return out


def execution(db: DB, run_id: int | None, decs_by_id: dict[int, dict[str, Any]]) -> dict[str, Any]:
    where = "WHERE run_id=?" if run_id else ""
    trades = db.query(f"SELECT * FROM trades {where} ORDER BY id", [run_id] if run_id else [])
    closed = [t for t in trades if t["status"] == "closed"]

    def dist(xs: list[float]) -> dict[str, Any] | None:
        xs = sorted(x for x in xs if x is not None and math.isfinite(x))
        if not xs:
            return None
        return {"n": len(xs), "mean": round(statistics.fmean(xs), 2), "p50": round(xs[len(xs) // 2], 2),
                "p90": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 2), "max": round(xs[-1], 2)}

    shortfall, fees_bp, impact, mk = [], [], [], {"1": [], "5": [], "15": []}
    for t in closed:
        side = 1 if t["side"] == "long" else -1
        if t.get("decision_price") and t.get("entry_price"):
            shortfall.append(side * (t["entry_price"] / t["decision_price"] - 1) * 10000)
        if t.get("notional") and t.get("fees") is not None:
            fees_bp.append(t["fees"] / t["notional"] * 10000)
        d = decs_by_id.get(t["decision_id"])
        meta = ((d or {}).get("decision_json") or {}).get("meta") or {}
        if meta.get("depth10_usd") and t.get("notional"):
            impact.append(t["notional"] / meta["depth10_usd"] * 10)  # bp if our size ate the 10 bp band linearly
        for k, v in (((d or {}).get("label_json") or {}).get("markout_bp") or {}).items():
            if k in mk and v is not None:
                mk[k].append(side * v)
    maker = [t for t in trades if t.get("entry_mode") == "maker"]
    m_filled = [t for t in maker if t["status"] not in ("unfilled", "error")]
    missed = [decs_by_id[t["decision_id"]] for t in maker if t["status"] == "unfilled" and t["decision_id"] in decs_by_id]
    missed_r = [(d.get("label_json") or {}).get(f"{d.get('action')}_r") for d in missed]
    missed_r = [r for r in missed_r if r is not None]
    return {
        "trades": len(trades), "closed": len(closed),
        "implementation_shortfall_bp": dist(shortfall),
        "entry_slip_vs_testnet_bp": dist([t["entry_slip_bp"] for t in closed]),
        "stop_exit_slip_bp": dist([t["exit_slip_bp"] for t in closed if (t["close_reason"] or "").startswith("stop")]),
        "fees_bp": dist(fees_bp),
        "impact_proxy_bp": dist(impact),
        "markout_bp_after_entry": {k: dist(v) for k, v in mk.items()},
        "maker": {"orders": len(maker), "filled": len(m_filled), "fill_rate": round(len(m_filled) / len(maker), 3) if maker else None,
                  "wait_ms": dist([t["entry_wait_ms"] for t in m_filled]),
                  "missed_trades": len(missed), "missed_mean_r": round(statistics.fmean(missed_r), 4) if missed_r else None},
        "entry_latency_ms": dist([t["entry_latency_ms"] for t in closed]),
    }


def validated_symbols(per_symbol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Gate used by the live policy: a symbol is validated when its bracket skill interval is above 0."""
    out = {}
    for sym, blk in per_symbol.items():
        b = blk.get("bracket") or {}
        ok = b.get("verdict") == "skill"
        why = {"skill": "bracket skill above 0 (90% interval)", "no skill": f"bracket skill {b.get('skill')} below base rates",
               "undecided": f"bracket skill {b.get('skill')}, interval {b.get('ci90')} includes 0"}.get(
            b.get("verdict"), f"only {b.get('independent_windows', 0)} independent windows (need {MIN_FIT})")
        out[sym] = {"ok": ok, "why": why}
    return out


def compute(db: DB, *, run_id: int | None = None, model: str | None = None, decision_tf: str | None = None,
            since_ts: float | None = None) -> dict[str, Any]:
    decs = load(db, run_id=run_id, model=model, decision_tf=decision_tf, since_ts=since_ts)
    lab = labelled(decs)
    ind = independent(lab)
    where = "WHERE run_id=?" if run_id else ""
    trades = db.query(f"SELECT * FROM trades {where}", [run_id] if run_id else [])
    by_dec = {t["decision_id"]: t for t in trades if t.get("decision_id")}
    symbols = sorted({d["symbol"] for d in ind})
    per_symbol = {}
    for sym in symbols:
        ds = [d for d in ind if d["symbol"] == sym]
        per_symbol[sym] = {g: skill_block(g, ds) for g in ("bracket", "direction", "big_move", "return_distribution")}
    overall = {g: skill_block(g, ind) for g in ("bracket", "direction", "big_move", "return_distribution")}

    def meta(d: dict[str, Any]) -> dict[str, Any]:
        return (d.get("decision_json") or {}).get("meta") or {}

    def hour(d: dict[str, Any]) -> int:
        return meta(d).get("hour_utc") if meta(d).get("hour_utc") is not None else int((d["ts"] % 86400) // 3600)

    return {
        "decisions": len(decs), "labelled": len(lab), "independent_windows": len(ind),
        "method": "independent windows: per symbol, a decision counts only if it starts after the previous counted one's horizon ends",
        "overall": overall,
        "per_symbol": per_symbol,
        "validated": validated_symbols(per_symbol),
        "by_hour_utc": group_table(ind, hour, by_dec, order=list(range(24))),
        "by_session": group_table(ind, lambda d: meta(d).get("session") or session_of(hour(d)), by_dec,
                                  order=["asia", "europe", "us", "late_us"]),
        "by_regime": group_table(ind, lambda d: meta(d).get("regime") or "unknown", by_dec),
        "by_event": group_table(ind, lambda d: meta(d).get("events") or ["none"], by_dec),
        "drift": drift(ind),
        "walk_forward": walk_forward(ind),
        "edge_vs_cost": edge_vs_cost(lab, set(by_dec)),
        "execution": execution(db, run_id, {d["id"]: d for d in decs}),
    }
