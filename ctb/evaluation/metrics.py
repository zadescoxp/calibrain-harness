"""Model-quality, consistency, operational and trading metrics, recomputed from raw records.

Every probabilistic metric is reported next to a baseline:
- climatology: the empirical outcome frequencies over the same labelled sample
  (in-sample, so it is a slightly *optimistic* baseline: beating it is meaningful)
- uniform: equal probability on every option
A model with Brier skill score <= 0 against climatology adds no information.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from typing import Any

from ..db import DB
from ..kairos import news as news_profile
from ..kairos.questions import BRACKET_IDS, RETURN_BIN_IDS


def _brier(probs: list[float], k: int) -> float:
    return sum((p - (1.0 if i == k else 0.0)) ** 2 for i, p in enumerate(probs))


def _logloss(probs: list[float], k: int) -> float:
    return -math.log(max(probs[k], 1e-6))


def _rps(probs: list[float], k: int) -> float:
    """Ranked probability score for ordered bins (lower is better)."""
    cp = co = 0.0
    s = 0.0
    for i in range(len(probs) - 1):
        cp += probs[i]
        co += 1.0 if i == k else 0.0
        s += (cp - co) ** 2
    return s / (len(probs) - 1)


def auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    ranked = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    # average ranks for ties
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rank_sum += avg * sum(1 for x in ranked[i:j] if x[1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def calibration(pred: list[float], obs: list[bool], bins: int = 10) -> dict[str, Any]:
    buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for p, y in zip(pred, obs):
        buckets[min(int(p * bins), bins - 1)].append((p, y))
    rows, ece, n = [], 0.0, len(pred)
    for b in range(bins):
        xs = buckets.get(b, [])
        if not xs:
            continue
        mp = sum(p for p, _ in xs) / len(xs)
        fo = sum(1 for _, y in xs if y) / len(xs)
        ece += len(xs) / n * abs(mp - fo)
        rows.append({"bin": f"{b / bins:.1f}-{(b + 1) / bins:.1f}", "mean_pred": round(mp, 4), "observed": round(fo, 4), "n": len(xs)})
    return {"bins": rows, "ece": round(ece, 4) if n else None}


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 5:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _r4(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def multiclass_block(name: str, rows: list[tuple[list[float], int]], n_opts: int, ordered: bool = False) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    freq = Counter(k for _, k in rows)
    clim = [freq.get(i, 0) / len(rows) for i in range(n_opts)]
    uni = [1 / n_opts] * n_opts
    score = _rps if ordered else _brier
    m = statistics.fmean(score(p, k) for p, k in rows)
    mc = statistics.fmean(score(clim, k) for _, k in rows)
    mu = statistics.fmean(score(uni, k) for _, k in rows)
    acc = statistics.fmean(1.0 if max(range(n_opts), key=lambda i: p[i]) == k else 0.0 for p, k in rows)
    acc_clim = max(clim)
    mean_pred = [statistics.fmean(p[i] for p, _ in rows) for i in range(n_opts)]
    entropy = statistics.fmean(-sum(x * math.log(x) for x in p if x > 0) for p, _ in rows)
    return {
        "name": name, "n": len(rows),
        ("rps" if ordered else "brier"): _r4(m),
        ("rps" if ordered else "brier") + "_climatology": _r4(mc),
        ("rps" if ordered else "brier") + "_uniform": _r4(mu),
        "skill_vs_climatology": _r4(1 - m / mc) if mc else None,
        "log_loss": _r4(statistics.fmean(_logloss(p, k) for p, k in rows)),
        "accuracy_argmax": _r4(acc), "accuracy_majority_baseline": _r4(acc_clim),
        "observed_freq": [_r4(x) for x in clim], "mean_predicted": [_r4(x) for x in mean_pred],
        "mean_entropy_nats": _r4(entropy), "max_entropy_nats": _r4(math.log(n_opts)),
        "prob_spread_std": _r4(statistics.pstdev([p[0] for p, _ in rows])) if len(rows) > 1 else None,
    }


def _dist(xs: list[float]) -> dict[str, Any] | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    q = lambda p: xs[min(len(xs) - 1, int(p * len(xs)))]  # noqa: E731
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 2), "p50": round(q(0.5), 2), "p90": round(q(0.9), 2), "max": round(xs[-1], 2)}


def execution_quality(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """How far real testnet fills were from the prices the strategy assumed."""
    filled = [t for t in trades if t.get("requested_qty")]
    return {
        "entry_slip_bp": _dist([t.get("entry_slip_bp") for t in trades]),
        "stop_exit_slip_bp": _dist([t.get("exit_slip_bp") for t in trades if t.get("close_reason") == "stop_loss"]),
        "target_exit_slip_bp": _dist([t.get("exit_slip_bp") for t in trades if t.get("close_reason") == "take_profit"]),
        "entry_latency_ms": _dist([t.get("entry_latency_ms") for t in trades]),
        "fill_ratio_mean": round(statistics.fmean(t["qty"] / t["requested_qty"] for t in filled), 4) if filled else None,
        "multi_fill_exits": sum(1 for t in trades if (t.get("exit_fills") or 1) > 1),
        "worst_r": round(min((t["r_mult"] for t in trades if t.get("r_mult") is not None), default=0), 3),
    }


def binary_block(name: str, rows: list[tuple[float, bool]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    base = statistics.fmean(1.0 if y else 0.0 for _, y in rows)
    bm = statistics.fmean((p - (1.0 if y else 0.0)) ** 2 for p, y in rows)
    bc = statistics.fmean((base - (1.0 if y else 0.0)) ** 2 for _, y in rows)
    return {"name": name, "n": len(rows), "brier": _r4(bm), "brier_climatology": _r4(bc),
            "skill_vs_climatology": _r4(1 - bm / bc) if bc else None, "auc": _r4(auc([p for p, _ in rows], [y for _, y in rows])),
            "base_rate": _r4(base), "mean_predicted": _r4(statistics.fmean(p for p, _ in rows)),
            "calibration": calibration([p for p, _ in rows], [y for _, y in rows])}


def compute(db: DB, *, symbol: str | None = None, since_ts: float | None = None, decision_tf: str | None = None,
            qset: str | None = None, run_id: int | None = None, model: str | None = None) -> dict[str, Any]:
    where, params = ["probe IS NULL"], []
    if model:
        where.append("model_provider=?")
        params.append(model)
    if run_id:
        where.append("run_id=?")
        params.append(run_id)
    if qset:
        where.append("qset_version LIKE ?")
        params.append(qset + "%")
    if decision_tf:
        where.append("decision_tf=?")
        params.append(decision_tf)
    if symbol:
        where.append("symbol=?")
        params.append(symbol)
    if since_ts:
        where.append("ts>=?")
        params.append(since_ts)
    w = " AND ".join(where)
    decs = db.query(f"SELECT id, ts, symbol, parsed_json, issues_json, usage_json, latency_ms, attempts, error_kind, error_msg, "
                    f"decision_json, action, label_json, sl_bp, tp_bp, model_meta_json, request_bytes, qset_version FROM decisions WHERE {w} ORDER BY ts", params)
    out: dict[str, Any] = {"generated_from": {"decisions": len(decs), "symbol": symbol, "decision_tf": decision_tf, "qset": qset, "run_id": run_id, "model": model}}

    # ---------------- ops
    ok = [d for d in decs if not d["error_kind"]]
    errs = Counter(d["error_kind"] for d in decs if d["error_kind"])
    issues = Counter()
    for d in ok:
        for i in d["issues_json"] or []:
            issues[i.split(":")[0] + ": " + i.split(":", 1)[1].split("(")[0].strip()[:60] if ":" in i else i] += 1
    lat = [d["latency_ms"] for d in ok if d["latency_ms"]]
    toks = [(d["usage_json"] or {}).get("input_tokens") for d in ok]
    toks = [t for t in toks if isinstance(t, (int, float))]
    versions = Counter(str((d["model_meta_json"] or {}).get("model_version") or (d["model_meta_json"] or {}).get("model")) for d in ok)
    out["ops"] = {
        "requests": len(decs), "ok": len(ok), "errors": dict(errs),
        "error_rate": _r4(len(decs) and (len(decs) - len(ok)) / len(decs)),
        "schema_issues": dict(issues.most_common(15)), "responses_with_issues": sum(1 for d in ok if d["issues_json"]),
        "latency_ms_p50": _r4(_pct(lat, 0.5)), "latency_ms_p90": _r4(_pct(lat, 0.9)), "latency_ms_p99": _r4(_pct(lat, 0.99)),
        "input_tokens_mean": _r4(statistics.fmean(toks)) if toks else None, "input_tokens_max": max(toks) if toks else None,
        "request_bytes_mean": _r4(statistics.fmean([d["request_bytes"] for d in ok if d["request_bytes"]])) if ok else None,
        "retried_requests": sum(1 for d in ok if (d["attempts"] or 1) > 1),
        "model_versions": dict(versions),
    }
    # answer distribution (does the model ever move?)
    actions = Counter(d["action"] for d in ok)
    out["policy"] = {"actions": dict(actions)}
    gate_counts = Counter()
    for d in ok:
        for g in (d["decision_json"] or {}).get("gates", []):
            gate_counts[g.split(" ")[0] + " " + (g.split(" ")[1] if len(g.split(" ")) > 1 else "")] += 1
    out["policy"]["gate_counts"] = dict(gate_counts.most_common(10))

    # ---------------- model quality on labelled decisions
    lab = [d for d in ok if d["label_json"] and not d["label_json"].get("unlabelable") and d["parsed_json"]]
    out["labelled"] = len(lab)
    blocks: dict[str, Any] = {}
    for side in ("long", "short"):
        qid = f"{side}_bracket"
        rows = [([d["parsed_json"][qid]["probs"][o] for o in BRACKET_IDS], BRACKET_IDS.index(d["label_json"][side]["outcome"]))
                for d in lab if qid in d["parsed_json"]]
        b = multiclass_block(qid, rows, 3)
        if rows:
            pt = [p[0] for p, _ in rows]
            yt = [k == 0 for _, k in rows]
            b["auc_target_first"] = _r4(auc(pt, yt))
            b["calibration_target_first"] = calibration(pt, yt)
            b["ambiguous_bars"] = sum(1 for d in lab if d["label_json"][side].get("ambiguous"))
        blocks[qid] = b
    rows = [([d["parsed_json"]["return_distribution"]["probs"][o] for o in RETURN_BIN_IDS], d["label_json"]["return_bin"])
            for d in lab if "return_distribution" in d["parsed_json"]]
    rb = multiclass_block("return_distribution", rows, 7, ordered=True)
    if rows:
        # PIT histogram: where the realised bin falls in the predicted CDF
        pit = Counter()
        for p, k in rows:
            pit[min(int((sum(p[:k]) + p[k] / 2) * 5), 4)] += 1
        rb["pit_histogram_quintiles"] = [pit.get(i, 0) for i in range(5)]
        em = [(d["decision_json"] or {}).get("expected_move_in_d") for d in lab if "return_distribution" in d["parsed_json"]]
        rets = [d["label_json"]["return_pct"] / (d["sl_bp"] / 100) for d in lab if "return_distribution" in d["parsed_json"]]
        pairs = [(a, b) for a, b in zip(em, rets) if a is not None]
        rb["spearman_expected_vs_realised"] = _r4(spearman([a for a, _ in pairs], [b for _, b in pairs]))
        rb["directional_hit_rate"] = _r4(statistics.fmean(1.0 if (a > 0) == (b > 0) else 0.0 for a, b in pairs if a != 0 and b != 0)) if pairs else None
    blocks["return_distribution"] = rb
    rows_r = [(d["parsed_json"]["regime"]["probs"], d["label_json"]["regime"]) for d in lab if "regime" in d["parsed_json"] and "regime" in d["label_json"]]
    if rows_r:
        ids = ["uptrend", "downtrend", "range", "volatile_chop"]
        blocks["regime"] = multiclass_block("regime (heuristic labels)", [([p[i] for i in ids], ids.index(y)) for p, y in rows_r], 4)
    mr = [(d["parsed_json"]["mean_reversion"]["p"], bool(d["label_json"]["reverted_to_ema"])) for d in lab
          if "mean_reversion" in d["parsed_json"] and "reverted_to_ema" in d["label_json"]]
    if mr:
        base = statistics.fmean(1.0 if y else 0.0 for _, y in mr)
        bm = statistics.fmean((p - (1.0 if y else 0.0)) ** 2 for p, y in mr)
        bc = statistics.fmean((base - (1.0 if y else 0.0)) ** 2 for _, y in mr)
        blocks["mean_reversion"] = {"n": len(mr), "brier": _r4(bm), "brier_climatology": _r4(bc), "skill_vs_climatology": _r4(1 - bm / bc) if bc else None,
                                    "auc": _r4(auc([p for p, _ in mr], [y for _, y in mr])), "base_rate": _r4(base),
                                    "calibration": calibration([p for p, _ in mr], [y for _, y in mr])}
    dr = [(d["parsed_json"]["direction"]["p"], d["label_json"]["return_pct"] > 0) for d in lab if "direction" in d["parsed_json"]]
    if dr:
        blocks["direction"] = binary_block("direction (price higher at horizon)", dr)
    bmv = [(d["parsed_json"]["big_move"]["p"], d["label_json"]["max_up_pct"] >= d["sl_bp"] / 100 or -d["label_json"]["max_down_pct"] >= d["sl_bp"] / 100)
           for d in lab if "big_move" in d["parsed_json"]]
    if bmv:
        blocks["big_move"] = binary_block("big_move (>= 1 stop either way)", bmv)
    out["model_quality"] = blocks

    # ---------------- v2: did Kairos add information beyond the base rates it was given?
    from ..calibration import CALIBRATED, fit, rows_for
    out["vs_base_rates"] = {q: r for q in CALIBRATED if (r := fit(rows_for(q, lab)))["n"]}

    # ---------------- does predicted EV rank realised R?  (the money question)
    ev_rows = []
    for d in lab:
        dj = d["decision_json"] or {}
        for side in ("long", "short"):
            ev = dj.get(f"ev_{side}_r")
            if ev is not None:
                ev_rows.append((ev, d["label_json"][f"{side}_r"], side))
    ev_block: dict[str, Any] = {"n": len(ev_rows)}
    if ev_rows:
        ev_block["spearman_ev_vs_realised_r"] = _r4(spearman([a for a, _, _ in ev_rows], [b for _, b, _ in ev_rows]))
        edges = [-math.inf, -0.5, -0.25, 0.0, 0.15, 0.3, math.inf]
        buckets = []
        for lo, hi in zip(edges, edges[1:]):
            xs = [r for e, r, _ in ev_rows if lo <= e < hi]
            if xs:
                buckets.append({"ev_bucket": f"[{lo}, {hi})", "n": len(xs), "mean_realised_r": _r4(statistics.fmean(xs)),
                                "win_rate": _r4(sum(1 for x in xs if x > 0) / len(xs))})
        ev_block["by_predicted_ev"] = buckets
        # baselines: always-long, always-short, random side
        ev_block["baseline_always_long_r"] = _r4(statistics.fmean(d["label_json"]["long_r"] for d in lab))
        ev_block["baseline_always_short_r"] = _r4(statistics.fmean(d["label_json"]["short_r"] for d in lab))
        chosen = []
        for d in lab:
            dj = d["decision_json"] or {}
            if dj.get("ev_long_r") is None:
                continue
            side = "long" if dj["ev_long_r"] >= dj["ev_short_r"] else "short"
            chosen.append(d["label_json"][f"{side}_r"])
        ev_block["model_best_side_every_bar_r"] = _r4(statistics.fmean(chosen)) if chosen else None
        taken = [d["label_json"][f"{d['action']}_r"] for d in lab if d["action"] in ("long", "short")]
        ev_block["policy_signals_n"] = len(taken)
        ev_block["policy_signals_mean_r"] = _r4(statistics.fmean(taken)) if taken else None
        ev_block["policy_signals_win_rate"] = _r4(sum(1 for x in taken if x > 0) / len(taken)) if taken else None
        for mode in ("strict", "exploration"):
            xs = []
            for d in lab:
                dj = d["decision_json"] or {}
                side = dj.get("strict_action") if mode == "strict" else (d["action"] if dj.get("exploration") else None)
                if side in ("long", "short"):
                    xs.append(d["label_json"][f"{side}_r"])
            ev_block[f"{mode}_signals_n"] = len(xs)
            ev_block[f"{mode}_signals_mean_r"] = _r4(statistics.fmean(xs)) if xs else None
            ev_block[f"{mode}_signals_win_rate"] = _r4(sum(1 for x in xs if x > 0) / len(xs)) if xs else None
        sq = defaultdict(list)
        for d in lab:
            dj = d["decision_json"] or {}
            if dj.get("setup_quality") is not None and dj.get("ev_long_r") is not None:
                side = "long" if dj["ev_long_r"] >= dj["ev_short_r"] else "short"
                sq[int(round(dj["setup_quality"]))].append(d["label_json"][f"{side}_r"])
        ev_block["best_side_r_by_setup_quality"] = {k: {"n": len(v), "mean_r": _r4(statistics.fmean(v))} for k, v in sorted(sq.items())}
    out["edge"] = ev_block

    # ---------------- internal consistency
    coh = [(d["decision_json"] or {}).get("coherence") for d in ok]
    coh = [c for c in coh if c is not None]
    both_pos = sum(1 for d in ok if (d["decision_json"] or {}).get("ev_long_r", -1) > 0 and (d["decision_json"] or {}).get("ev_short_r", -1) > 0)
    contra = 0
    for d in ok:
        p = d["parsed_json"] or {}
        if "long_bracket" in p and "short_bracket" in p:
            if p["long_bracket"]["probs"]["target_first"] > 0.5 and p["short_bracket"]["probs"]["target_first"] > 0.5:
                contra += 1
    out["consistency"] = {
        "coherence_mean": _r4(statistics.fmean(coh)) if coh else None,
        "both_sides_positive_ev": both_pos,
        "both_targets_above_50pct": contra,
        **probe_metrics(db),
    }

    # ---------------- trading (testnet, actual fills, fees and slippage)
    tw, tp = ["status='closed'"], []
    if symbol:
        tw.append("symbol=?")
        tp.append(symbol)
    if run_id:
        tw.append("run_id=?")
        tp.append(run_id)
    trades = db.query("SELECT * FROM trades WHERE " + " AND ".join(tw) + " ORDER BY closed_ts", tp)
    tb: dict[str, Any] = {"closed": len(trades)}
    if trades:
        pnl = [t["realized_pnl"] or 0 for t in trades]
        rs = [t["r_mult"] if t.get("r_mult") is not None else (t["realized_pnl"] or 0) / t["risk_usd"] for t in trades if t.get("r_mult") is not None or t.get("risk_usd")]
        wins = [x for x in pnl if x > 0]
        losses = [x for x in pnl if x <= 0]
        eq, peak, mdd = 0.0, 0.0, 0.0
        for x in pnl:
            eq += x
            peak = max(peak, eq)
            mdd = min(mdd, eq - peak)
        tb.update({
            "net_pnl_usdt": round(sum(pnl), 2), "fees_usdt": round(sum(t["fees"] or 0 for t in trades), 2),
            "win_rate": _r4(len(wins) / len(pnl)), "profit_factor": _r4(sum(wins) / -sum(losses)) if losses and sum(losses) else None,
            "avg_r": _r4(statistics.fmean(rs)) if rs else None,
            "avg_predicted_ev_r": _r4(statistics.fmean(t["ev_r"] for t in trades if t["ev_r"] is not None)),
            "max_drawdown_usdt": round(mdd, 2),
            "by_reason": dict(Counter(t["close_reason"] for t in trades)),
            "by_mode": {m: {"n": len(v), "pnl": round(sum(v), 2), "win_rate": _r4(sum(1 for x in v if x > 0) / len(v))}
                        for m in ("strict", "exploration") if (v := [t["realized_pnl"] or 0 for t in trades if (t.get("mode") or t.get("notes") or "strict") == m])},
            "by_side": {s: {"n": len(v), "pnl": round(sum(v), 2)} for s in ("long", "short") if (v := [t["realized_pnl"] or 0 for t in trades if t["side"] == s])},
            "by_symbol": {s: round(sum(t["realized_pnl"] or 0 for t in trades if t["symbol"] == s), 2) for s in sorted({t["symbol"] for t in trades})},
            "execution": execution_quality(trades),
        })
    aw = [w for w in tw if w != "status='closed'"]
    attempts = db.query("SELECT status, COUNT(*) n FROM trades" + (" WHERE " + " AND ".join(aw) if aw else "") + " GROUP BY status", tp)
    tb["attempts_by_status"] = {r["status"]: r["n"] for r in attempts}
    out["trading"] = tb
    out["news"] = news_metrics(db)
    return out


def probe_metrics(db: DB) -> dict[str, Any]:
    probes = db.query("SELECT p.probe, p.parsed_json AS pp, o.parsed_json AS op FROM decisions p JOIN decisions o ON p.probe_of=o.id "
                      "WHERE p.probe IS NOT NULL AND p.error_kind IS NULL AND o.error_kind IS NULL")
    rep, mir = [], []
    for r in probes:
        pp = json.loads(r["pp"]) if isinstance(r["pp"], str) else (r["pp"] or {})
        op = json.loads(r["op"]) if isinstance(r["op"], str) else (r["op"] or {})
        if r["probe"] == "repeat":
            diffs = []
            for qid, a in op.items():
                b = pp.get(qid)
                if not b:
                    continue
                if "probs" in a and isinstance(a["probs"], dict):
                    diffs += [abs(a["probs"][k] - b["probs"].get(k, 0)) for k in a["probs"]]
                elif "p" in a:
                    diffs.append(abs(a["p"] - b["p"]))
                elif "score" in a:
                    diffs.append(abs(a["score"] - b["score"]) / 4)
            if diffs:
                rep.append(max(diffs))
        elif r["probe"] in ("mirror", "mirror_pair") and "long_bracket" in op and "short_bracket" in pp:
            # mirrored market: P(long target | orig) should equal P(short target | mirror)
            mir.append((op["long_bracket"]["probs"]["target_first"], pp["short_bracket"]["probs"]["target_first"],
                        op["short_bracket"]["probs"]["target_first"], pp["long_bracket"]["probs"]["target_first"]))
    out: dict[str, Any] = {"repeat_probes": len(rep), "mirror_probes": len(mir)}
    if rep:
        out["repeat_max_abs_prob_diff_mean"] = _r4(statistics.fmean(rep))
        out["repeat_identical_share"] = _r4(sum(1 for x in rep if x < 1e-6) / len(rep))
    if mir:
        errs = [abs(a - b) for a, b, _, _ in mir] + [abs(c - d) for _, _, c, d in mir]
        out["mirror_abs_error_mean"] = _r4(statistics.fmean(errs))
        lean_o = [a - c for a, _, c, _ in mir]
        lean_m = [d - b for _, b, _, d in mir]
        out["mirror_lean_flips_sign_share"] = _r4(sum(1 for x, y in zip(lean_o, lean_m) if x * y < 0 or (abs(x) < 0.02 and abs(y) < 0.02)) / len(mir))
    return out


def news_metrics(db: DB) -> dict[str, Any]:
    rows = db.query("SELECT * FROM news")
    done = [r for r in rows if r["probs_json"] and r["label_json"]]
    out: dict[str, Any] = {"requests": len(rows), "ok": sum(1 for r in rows if r["probs_json"]),
                           "errors": dict(Counter(r["error_kind"] for r in rows if r["error_kind"])), "labelled": len(done)}
    if done:
        b = multiclass_block("news_return", [(r["probs_json"], r["label_json"]["bin"]) for r in done], 7, ordered=True)
        up = [sum(r["probs_json"][4:]) - sum(r["probs_json"][:3]) for r in done]
        real = [r["label_json"]["return_bp"] for r in done]
        b["spearman_lean_vs_return"] = _r4(spearman(up, real))
        mids = [news_profile.bin_midpoints_bp(r["cost_bp"]) for r in done]
        b["mean_expected_bp"] = _r4(statistics.fmean(sum(p * m for p, m in zip(r["probs_json"], mm)) for r, mm in zip(done, mids)))
        b["mean_realised_bp"] = _r4(statistics.fmean(real))
        out["quality"] = b
    return out
