"""Policy: turn model probabilities into a trade decision. All arithmetic, thresholds and risk
rules live here in code, never in the model.

Expected value of a bracket, in R (1R = the stop distance), with each outcome charged its own cost
(costs.bracket_ev):
    EV = P(t) * (RR - c_t/SL) - P(s) * (1 + c_s/SL) - P(n) * c_n/SL

EV is computed on CALIBRATED probabilities: the models blended with the instrument's measured base
rates using weights fitted on independent labelled outcomes (calibration.py). The raw-model EV and the
base-rate-only EV are recorded next to it so the evaluation can tell whether the model added anything.

Gates come in three kinds:
- hard: safety and feasibility (position already open, venue divergence, liquidity, regime filter,
  drift pause). Nothing trades through them.
- filters: when to trade at all (a market event on the bar, a move likely enough to reach the
  bracket, an order-flow direction). They apply to strict and exploration trades alike.
- quality: whether the edge is proven (EV after costs, setup quality, direction agreement, validated
  out-of-sample skill). Strict trades need all of them; exploration may trade through them at
  reduced size, to test the model's lean with real fills.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .calibration import PRIOR_W
from .config import Settings
from .costs import bracket_ev
from .ensemble import blend_scalar, blend_sources, source_probs
from .kairos.questions import BRACKET_IDS, RETURN_BIN_EDGES_IN_D


@dataclass
class Decision:
    action: str  # long | short | flat
    reason: str
    ev_long_r: float | None = None
    ev_short_r: float | None = None
    p_long_target: float | None = None
    p_long_stop: float | None = None
    p_short_target: float | None = None
    p_short_stop: float | None = None
    expected_move_in_d: float | None = None
    setup_quality: float | None = None
    data_sufficient: float | None = None
    coherence: float | None = None
    gates: list[str] = field(default_factory=list)
    strict_action: str = "flat"  # what the strict policy alone would have done
    exploration: bool = False  # True when this trade exists only because of exploration mode
    ev_long_raw_r: float | None = None
    ev_short_raw_r: float | None = None
    ev_long_base_r: float | None = None
    ev_short_base_r: float | None = None
    p_up: float | None = None  # calibrated P(price higher at horizon)
    p_up_raw: float | None = None
    p_big_move: float | None = None
    p_big_move_base: float | None = None
    weights: dict[str, Any] = field(default_factory=dict)
    # v3
    model_side: str | None = None  # side the calibrated EV prefers
    side_source: str = "model"  # model | flow
    flow_score: float | None = None
    events: list[str] = field(default_factory=list)
    regime: str | None = None
    gate_kinds: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def expected_move_in_d(probs: dict[str, float]) -> float:
    """Mean of the return distribution, in multiples of the stop distance d."""
    e = RETURN_BIN_EDGES_IN_D
    mids = [e[0] * 1.25, (e[0] + e[1]) / 2, (e[1] + e[2]) / 2, 0.0, (e[3] + e[4]) / 2, (e[4] + e[5]) / 2, e[5] * 1.25]
    return sum(mids[i] * probs[f"bin_{i}"] for i in range(7))


def _costs(meta: dict[str, Any]) -> Any:
    return meta.get("costs") or meta.get("cost_bp", 15.0)


def decide(parsed: dict[str, Any], meta: dict[str, Any], s: Settings, *, has_position: bool,
           weights: dict[str, Any] | None = None, members: dict[str, dict[str, Any]] | None = None) -> Decision:
    required = ("long_bracket", "short_bracket", "setup_quality")
    missing = [q for q in required if q not in parsed]
    if missing:
        return Decision("flat", f"invalid/missing answers: {', '.join(missing)} (treated as error, not as a 'no')")

    sl = meta["sl_bp"]
    rr = meta["tp_bp"] / sl
    costs = _costs(meta)
    w: dict[str, Any] = {q: {"model": PRIOR_W} for q in ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")}
    w.update(weights or {})
    sources = {"model": parsed, **(members or {})}
    base = meta.get("base_rates")

    def bracket_probs(q: str) -> dict[str, float]:
        src = source_probs(q, sources)
        return blend_sources(src, base[q], w[q]) if base else src["model"]

    lb_raw, sb_raw = parsed["long_bracket"]["probs"], parsed["short_bracket"]["probs"]
    ev_l_raw = bracket_ev(lb_raw["target_first"], lb_raw["stop_first"], rr, sl, costs)
    ev_s_raw = bracket_ev(sb_raw["target_first"], sb_raw["stop_first"], rr, sl, costs)
    lb, sb = bracket_probs("long_bracket"), bracket_probs("short_bracket")
    ev_l = bracket_ev(lb["target_first"], lb["stop_first"], rr, sl, costs)
    ev_s = bracket_ev(sb["target_first"], sb["stop_first"], rr, sl, costs)
    # Coherence: a long's target and a short's stop are both "price goes up".
    coherence = 1 - abs(lb["target_first"] - sb["stop_first"]) if abs(rr - 1) < 1e-9 else 1 - abs(
        (lb["target_first"] - lb["stop_first"]) + (sb["target_first"] - sb["stop_first"])) / 2
    rd = parsed.get("return_distribution", {}).get("probs")
    if rd and base:
        rd = blend_sources(source_probs("return_distribution", sources), base["return_distribution"], w["return_distribution"])
    em = expected_move_in_d(rd) if rd else None
    p_up_raw = parsed["direction"]["p"] if "direction" in parsed else None
    p_up = (blend_scalar(source_probs("direction", sources), base["price_higher_at_horizon"], w["direction"])
            if p_up_raw is not None and base else p_up_raw)
    p_mv_raw = parsed["big_move"]["p"] if "big_move" in parsed else None
    p_mv_base = base["moved_at_least_one_stop_either_way"] if base else None
    p_mv = (blend_scalar(source_probs("big_move", sources), p_mv_base, w["big_move"])
            if p_mv_raw is not None and base else p_mv_raw)
    d = Decision(
        "flat", "", ev_long_r=round(ev_l, 4), ev_short_r=round(ev_s, 4),
        p_long_target=round(lb["target_first"], 4), p_long_stop=round(lb["stop_first"], 4),
        p_short_target=round(sb["target_first"], 4), p_short_stop=round(sb["stop_first"], 4),
        expected_move_in_d=round(em, 4) if em is not None else None,
        setup_quality=parsed["setup_quality"]["score"],
        data_sufficient=parsed["data_sufficient"]["p"] if "data_sufficient" in parsed else None,
        coherence=round(coherence, 4),
        ev_long_raw_r=round(ev_l_raw, 4), ev_short_raw_r=round(ev_s_raw, 4),
        p_up=round(p_up, 4) if p_up is not None else None, p_up_raw=p_up_raw,
        p_big_move=round(p_mv, 4) if p_mv is not None else None, p_big_move_base=p_mv_base,
        weights=w if base else {}, flow_score=meta.get("flow_score"), events=list(meta.get("events") or []),
        regime=meta.get("regime"),
    )
    if base:
        d.ev_long_base_r = round(bracket_ev(base["long_bracket"]["target_first"], base["long_bracket"]["stop_first"], rr, sl, costs), 4)
        d.ev_short_base_r = round(bracket_ev(base["short_bracket"]["target_first"], base["short_bracket"]["stop_first"], rr, sl, costs), 4)

    model_side = "long" if ev_l >= ev_s else "short"
    d.model_side = model_side
    side = model_side
    fs = meta.get("flow_score")
    flow_side = None if fs is None or abs(fs) < s.min_flow_score else ("long" if fs > 0 else "short")
    if s.direction_source == "flow":
        d.side_source = "flow"
        side = flow_side or model_side
    ev = ev_l if side == "long" else ev_s
    lean = abs(ev_l - ev_s)

    # ---- hard gates: safety / feasibility, never bypassed
    hard: list[str] = []
    if side == "short" and not s.allow_short:
        hard.append("shorts disabled")
    if has_position:
        hard.append("already in a position on this symbol")
    div = meta.get("venue_divergence_bp")
    if div is not None and abs(div) > s.max_venue_divergence_bp:
        hard.append(f"testnet/mainnet divergence {div:+.0f}bp > {s.max_venue_divergence_bp}bp")
    spread = meta.get("mainnet_spread_bp")
    if spread is not None and spread > s.max_spread_bp:
        hard.append(f"liquidity: mainnet spread {spread:.1f}bp > {s.max_spread_bp:g}bp")
    tspread = meta.get("testnet_spread_bp")
    if tspread is not None and tspread > s.max_testnet_spread_bp:
        hard.append(f"liquidity: testnet spread {tspread:.1f}bp > {s.max_testnet_spread_bp:g}bp")
    depth, notional = meta.get("depth10_usd"), meta.get("planned_notional")
    if depth is not None and notional and depth < s.min_depth_mult * notional:
        hard.append(f"liquidity: mainnet depth within 10bp ${depth:,.0f} < {s.min_depth_mult:g}x our ${notional:,.0f}")
    if s.allowed_regimes and meta.get("regime") and meta["regime"] not in s.allowed_regimes:
        hard.append(f"regime {meta['regime']} not in allowed regimes")
    drift = meta.get("drift") or {}
    if drift.get("alarm") and s.drift_action == "pause":
        hard.append(f"model drift detected ({', '.join(drift.get('questions', []))}): trading paused")

    # ---- filters: when to trade at all (strict and exploration)
    filters: list[str] = []
    if s.trade_trigger == "events" and not meta.get("events"):
        filters.append("no market event on this bar")
    if s.require_big_move and p_mv is not None and p_mv_base is not None and p_mv < p_mv_base + s.big_move_margin:
        filters.append(f"move unlikely: P(move >= 1 stop) {p_mv:.2f} < base {p_mv_base:.2f} + {s.big_move_margin:g}")
    if s.direction_source == "flow" and flow_side is None:
        filters.append(f"order flow has no direction (|flow score| {abs(fs or 0):.2f} < {s.min_flow_score:g})")
    if s.direction_source == "agree" and flow_side != model_side:
        filters.append(f"order flow ({flow_side or 'none'}) disagrees with the model ({model_side})")

    # ---- quality gates: the strict policy's judgment of whether the edge is real
    soft: list[str] = []
    if ev < s.min_edge_r:
        soft.append(f"best EV {ev:+.3f}R < min edge {s.min_edge_r}R")
    if d.setup_quality < s.min_setup_quality:
        soft.append(f"setup_quality {d.setup_quality:.2f} < {s.min_setup_quality}")
    if not base:
        soft.append("no base rates for this bracket yet (not enough history): probabilities cannot be calibrated")
    if d.p_up is not None:
        need = 0.5 + s.min_direction_margin
        if side == "long" and d.p_up < need:
            soft.append(f"direction P(up) {d.p_up:.2f} < {need:.2f} for a long")
        if side == "short" and d.p_up > 1 - need:
            soft.append(f"direction P(up) {d.p_up:.2f} > {1 - need:.2f} for a short")
    if em is not None and ((side == "long" and em < 0) or (side == "short" and em > 0)):
        soft.append(f"return distribution mean ({em:+.2f}d) disagrees with {side} bracket")
    val = meta.get("validated")
    if s.require_validated and val is not None and not val.get("ok"):
        soft.append(f"not validated: {val.get('why', 'no out-of-sample skill yet')}")
    if drift.get("alarm") and s.drift_action != "pause":
        soft.append(f"model drift detected ({', '.join(drift.get('questions', []))})")

    d.gates = hard + filters + soft
    d.gate_kinds = {"hard": hard, "filters": filters, "quality": soft}
    p_t = lb["target_first"] if side == "long" else sb["target_first"]

    if not d.gates:
        d.action = d.strict_action = side
        d.reason = (f"{side} EV {ev:+.3f}R calibrated (raw {ev_l_raw if side == 'long' else ev_s_raw:+.3f}R, "
                    f"P target {p_t:.2f}, RR {rr:.2f}, side from {d.side_source})")
        return d
    min_lean = s.explore_min_lean_r if d.side_source == "model" else 0.0
    if s.exploration_mode and not hard and not filters and lean >= min_lean:
        d.action, d.exploration = side, True
        d.reason = (f"EXPLORE {side}: {d.side_source} prefers {side}"
                    + (f" by {lean:.3f}R" if d.side_source == "model" else f" (flow score {fs:+.2f})")
                    + f" (EV {ev:+.3f}R, P target {p_t:.2f}); strict gates would block: {'; '.join(soft)}")
        return d
    if s.exploration_mode and not hard and not filters:
        d.gates.append(f"explore: lean {lean:.3f}R < {s.explore_min_lean_r}R")
    d.reason = "; ".join(d.gates)
    return d


# ---------------------------------------------------------------------------- sizing


def size_position(equity: float, sl_bp: float, price: float, s: Settings, risk_pct: float | None = None) -> tuple[float, float]:
    """Risk-based sizing. Returns (qty_base, notional_usdt)."""
    risk_usd = equity * (s.risk_per_trade_pct if risk_pct is None else risk_pct) / 100
    notional = risk_usd / (sl_bp / 10000)
    notional = min(notional, s.max_notional_usdt, equity * s.max_leverage * 0.9)
    return notional / price, notional


def risk_pct_for(s: Settings, *, exploration: bool, ev_r: float | None = None, vol_state: str | None = None,
                 run_drawdown_pct: float = 0.0, same_side_corr: list[float] | None = None) -> tuple[float, dict[str, Any]]:
    """Dynamic risk per trade (% of equity) and how it was set.

    base:        risk_per_trade_pct (strict) or explore_risk_pct (exploration)
    confidence:  strict only, EV / min_edge_r clipped to [0.5, 1.5] (more edge, more size; capped)
    regime:      0.5 when the volatility regime is high (stops are wider and fills worse)
    drawdown:    0.5 once the run is down more than dd_throttle_pct of equity
    correlation: 1 / (1 + sum of positive correlations with open same-side positions): four longs on
                 coins that move with BTC are closer to one bet than four, so each gets a share of one
    """
    base = min(s.explore_risk_pct, s.risk_per_trade_pct) if exploration else s.risk_per_trade_pct
    parts: dict[str, Any] = {"base_pct": base}
    if not s.dynamic_sizing:
        parts["risk_pct"] = base
        return base, parts
    conf = 1.0
    if not exploration and ev_r is not None and s.min_edge_r > 0:
        conf = max(0.5, min(1.5, ev_r / s.min_edge_r))
    regime = 0.5 if vol_state == "high" else 1.0
    dd = 0.5 if run_drawdown_pct >= s.dd_throttle_pct > 0 else 1.0
    corr = 1.0 / (1.0 + sum(max(0.0, c) for c in (same_side_corr or [])))
    parts.update({"confidence": round(conf, 3), "regime": regime, "drawdown": dd, "correlation": round(corr, 3)})
    pct = base * conf * regime * dd * corr
    parts["risk_pct"] = round(pct, 4)
    return pct, parts


__all__ = ["Decision", "decide", "size_position", "risk_pct_for", "expected_move_in_d", "BRACKET_IDS"]
