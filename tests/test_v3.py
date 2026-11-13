"""Tests for the v3 strategy layer: volatility and flow models, brackets, costs, symmetrization,
calibration on independent windows, policy filters, sizing, validation and executor changes."""

import asyncio
import math
import random
import time

import pytest

from ctb.config import Settings
from ctb.db import DB


def v3() -> Settings:
    s = Settings()
    s.exploration_mode = False
    s.allowed_regimes = []
    s.ensemble = []
    return s


# ---------------------------------------------------------------- GARCH
def _simulate_garch(n, omega, alpha, beta, seed=1):
    rng = random.Random(seed)
    v = omega / (1 - alpha - beta)
    out = []
    for _ in range(n):
        x = math.sqrt(v) * rng.gauss(0, 1)
        out.append(x)
        v = omega + alpha * x * x + beta * v
    return out


def test_garch_recovers_parameters_and_mean_reverts():
    from ctb.quant import fit_garch
    r = _simulate_garch(3000, 1e-7, 0.12, 0.83)
    g = fit_garch(r)
    assert abs(g.alpha - 0.12) < 0.06 and abs(g.beta - 0.83) < 0.08
    # horizon variance is the sum of per-step forecasts, which revert to the long-run level
    g.var_last = 4 * g.uncond_var
    steps = g.forecast_var(200)
    assert steps[0] == pytest.approx(4 * g.uncond_var) and steps[-1] == pytest.approx(g.uncond_var, rel=0.05)
    assert g.horizon_sigma(30) == pytest.approx(math.sqrt(sum(g.forecast_var(30))))
    assert fit_garch(r[:50]) is None


# ---------------------------------------------------------------- Hawkes
def _simulate_hawkes(mu, n, beta, T, seed=3):
    """Ogata thinning for an exponential-kernel Hawkes process (alpha = n * beta)."""
    rng = random.Random(seed)
    alpha = n * beta
    t, events, excite = 0.0, [], 0.0
    while t < T:
        lam_bar = mu + excite
        w = rng.expovariate(lam_bar)
        excite *= math.exp(-beta * w)
        t += w
        if rng.random() * lam_bar <= mu + excite:
            events.append(t)
            excite += alpha
    return events


def test_hawkes_fit_tells_self_exciting_flow_from_poisson():
    from ctb.quant import fit_hawkes
    excited = fit_hawkes(_simulate_hawkes(0.5, 0.7, 1.0, 3000))
    poisson = fit_hawkes(_simulate_hawkes(1.5, 0.0, 1.0, 1500))
    assert excited.branching >= 0.5 and excited.beta in (0.2, 1.0, 5.0)
    assert poisson.branching <= 0.2
    assert fit_hawkes([1.0, 2.0]) is None


def test_flow_intensity_imbalance_and_duplicate_merge():
    from ctb.quant import FlowIntensity
    fi = FlowIntensity()
    t0 = 1000.0
    for i in range(50):
        fi.add(t0 + i * 0.1, "Buy")
        fi.add(t0 + i * 0.1, "Buy")  # same aggressive order, several fills: counted once
    for i in range(10):
        fi.add(t0 + i * 0.5, "Sell")
    f = fi.features(t0 + 5.0)
    assert f["intensity_imbalance_5s"] > 0.3 and len(fi.events["Buy"]) == 50


def test_queue_model_learns_that_thin_ask_queue_moves_mid_up():
    from ctb.quant import QueueModel
    q = QueueModel()
    rng = random.Random(5)
    mid = 100.0
    for _ in range(3000):
        imb = rng.random()
        q.update((mid - 0.05, imb * 10), (mid + 0.05, (1 - imb) * 10))
        mid += 0.1 if rng.random() < 0.2 + 0.6 * imb else -0.1  # heavy bid queue -> mid tends to rise
        q.update((mid - 0.05, 5), (mid + 0.05, 5))
    assert q.p_up(0.95) > 0.65 and q.p_up(0.05) < 0.35
    assert q.features()["learned_from_mid_moves"] > 500  # a decayed count: recent moves weigh most


def test_regime_classification():
    from ctb.quant import classify_regime, correlation
    up = [100 + i for i in range(60)]
    chop = [100 + (1 if i % 2 else -1) for i in range(60)]
    assert classify_regime(0.9, up)["regime"] == "trend_up/high_vol"
    assert classify_regime(0.5, chop)["regime"] == "range/normal_vol"
    assert correlation([1, 2, 3] * 10, [2, 4, 6] * 10) == pytest.approx(1.0)


# ---------------------------------------------------------------- brackets
def _store_with_bars(closes_1m, symbol="BTCUSDT"):
    from ctb.market.store import Bar, MarketStore
    st = MarketStore([symbol])
    t0 = 1_700_000_000_000
    bars1 = [Bar(t0 + i * 60000, c, c * 1.0004, c * 0.9996, c, 10.0, 10.0 * c) for i, c in enumerate(closes_1m)]
    st[symbol].bars["1"] = bars1
    bars5 = []
    for i in range(0, len(bars1) - 4, 5):
        g = bars1[i:i + 5]
        bars5.append(Bar(g[0].t, g[0].o, max(b.h for b in g), min(b.l for b in g), g[-1].c, 50.0, 50.0 * g[-1].c))
    st[symbol].bars["5"] = bars5
    return st


def test_vol_bracket_respects_cost_floor_and_extends_horizon():
    from ctb.analytics import MarketModels
    from ctb.costs import CostModel
    from ctb.features import bracket
    rng = random.Random(2)
    c, closes = 100.0, []
    for _ in range(900):
        c *= math.exp(rng.gauss(0, 0.0004))  # ~4 bp per 1m bar
        closes.append(c)
    store = _store_with_bars(closes)
    s = v3()
    s.decision_tf, s.horizon_bars, s.atr_tf = "1", 30, "5"
    models = MarketModels(store)
    costs = CostModel(s).outcome_costs("taker")
    b = bracket(store["BTCUSDT"], s, models, costs)
    floor = costs["reference_bp"] / s.max_cost_r
    assert b["mode"] == "vol" and b["sl_bp"] >= floor - 0.1
    assert b["cost_floor_bound"] and b["horizon_bars"] > 30  # 4bp x sqrt(30) ~ 22bp < floor: horizon extends
    assert s.sl_sigma * b["sigma_horizon_bp"] >= b["sl_bp"] - 0.5 or b["horizon_bars"] == int(30 * s.max_horizon_mult)
    assert b["tp_bp"] == pytest.approx(b["sl_bp"] * s.reward_risk, abs=0.2)
    s.bracket_mode = "atr"
    assert bracket(store["BTCUSDT"], s, models, costs)["mode"] == "atr"


def test_base_rates_can_be_conditioned_on_entries():
    from ctb.features import base_rates
    from ctb.market.store import Bar
    bars = [Bar(i * 60000, 100 + i * 0.1, 100 + i * 0.1 + 0.05, 100 + i * 0.1 - 0.05, 100 + i * 0.1, 1, 1) for i in range(300)]
    full = base_rates(bars, 10, 20, 30)
    some = base_rates(bars, 10, 20, 30, entries=set(range(0, 300, 5)), min_windows=30)
    assert full["windows"] > some["windows"] >= 30
    assert base_rates(bars, 10, 20, 30, entries={1, 2}, min_windows=30) is None


# ---------------------------------------------------------------- costs
def test_outcome_costs_and_ev():
    from ctb.costs import CostModel, bracket_ev
    s = v3()
    taker = CostModel(s).outcome_costs("taker")
    maker = CostModel(s).outcome_costs("maker")
    assert maker["entry_bp"] == s.maker_fee_bp and maker["stop_bp"] < taker["stop_bp"]
    assert taker["target_bp"] < taker["time_bp"] < taker["stop_bp"]  # resting target is the cheapest exit
    # a flat cost reproduces the old formula EV = pt*RR - ps - c/SL
    assert bracket_ev(0.4, 0.3, 1.5, 50, 10.0) == pytest.approx(0.4 * 1.5 - 0.3 - 10 / 50)
    ev = bracket_ev(0.4, 0.3, 1.5, 50, maker)
    assert ev == pytest.approx(0.4 * (1.5 - maker["target_bp"] / 50) - 0.3 * (1 + maker["stop_bp"] / 50) - 0.3 * maker["time_bp"] / 50)


def test_cost_model_uses_measured_slippage(tmp_path):
    from ctb.costs import MIN_MEASURED, CostModel
    db = DB(tmp_path / "t.db")
    for i in range(MIN_MEASURED + 2):
        db.insert("trades", {"symbol": "X", "side": "long", "status": "closed", "closed_ts": i, "entry_slip_bp": 3.0,
                             "exit_slip_bp": 20.0, "close_reason": "stop_loss", "entry_mode": "taker"})
    s = v3()
    c = CostModel(s, db).outcome_costs("taker")
    assert c["source"] == {"entry_slip": "measured", "stop_slip": "measured"}
    assert c["stop_bp"] == pytest.approx(s.taker_fee_bp + 3.0 + s.taker_fee_bp + 20.0)


# ---------------------------------------------------------------- symmetrize / ensemble
def _parsed(lt, ls, st, ss, up=0.5, mv=0.4):
    return {"long_bracket": {"probs": {"target_first": lt, "stop_first": ls, "neither": 1 - lt - ls}},
            "short_bracket": {"probs": {"target_first": st, "stop_first": ss, "neither": 1 - st - ss}},
            "direction": {"p": up}, "big_move": {"p": mv}, "setup_quality": {"score": 2.0},
            "return_distribution": {"probs": {f"bin_{i}": p for i, p in enumerate([0.05, 0.1, 0.15, 0.2, 0.25, 0.15, 0.1])}}}


def test_symmetrize_cancels_a_constant_long_bias():
    from ctb.ensemble import lean, symmetrize
    # a symmetric market read by a model that always adds +0.1 to the long target
    real = _parsed(0.35, 0.25, 0.25, 0.35, up=0.6)
    mirror = _parsed(0.35, 0.25, 0.25, 0.35, up=0.6)  # same bias on the mirrored market
    sym = symmetrize(real, mirror)
    assert lean(real) == pytest.approx(0.1) and lean(sym) == pytest.approx(0.0)
    assert sym["direction"]["p"] == pytest.approx(0.5)
    rd = sym["return_distribution"]["probs"]
    assert rd["bin_0"] == pytest.approx(rd["bin_6"])
    assert symmetrize(real, None) is real


def test_blend_sources_simplex():
    from ctb.ensemble import blend_scalar, blend_sources
    base = {"a": 0.5, "b": 0.5}
    src = {"model": {"a": 1.0, "b": 0.0}, "kairos": {"a": 0.0, "b": 1.0}}
    assert blend_sources(src, base, {"model": 0.5, "kairos": 0.25}) == pytest.approx({"a": 0.625, "b": 0.375})
    assert blend_sources(src, base, 0.0) == pytest.approx(base)
    assert blend_scalar({"model": 0.9}, 0.5, {"model": 0.5}) == pytest.approx(0.7)


# ---------------------------------------------------------------- calibration
def test_independent_windows_thin_overlapping_decisions():
    from ctb.calibration import independent
    decs = [{"ts": i * 60, "symbol": "A", "horizon_min": 30} for i in range(120)] + \
           [{"ts": i * 60, "symbol": "B", "horizon_min": 30} for i in range(60)]
    kept = independent(decs)
    assert sum(1 for d in kept if d["symbol"] == "A") == 4 and sum(1 for d in kept if d["symbol"] == "B") == 2


def test_fit_two_sources_finds_the_informative_one():
    from ctb.calibration import fit
    rows = []
    for i in range(200):
        y = i % 2
        good = [1.0, 0.0] if y == 0 else [0.0, 1.0]
        rows.append(([[0.9, 0.1], good], [0.5, 0.5], y))  # source 0 useless, source 1 perfect
    res = fit(rows, ("model", "kairos"))
    assert res["weights"] == {"model": 0.0, "kairos": 1.0}


def test_bracket_weight_is_shared_by_long_and_short(tmp_path):
    from ctb.calibration import MIN_FIT, Calibrator
    db = DB(tmp_path / "t.db")
    base = {"long_bracket": {"target_first": 0.3, "stop_first": 0.3, "neither": 0.4},
            "short_bracket": {"target_first": 0.3, "stop_first": 0.3, "neither": 0.4},
            "return_distribution": {f"bin_{i}": 1 / 7 for i in range(7)}, "price_higher_at_horizon": 0.5,
            "moved_at_least_one_stop_either_way": 0.6}
    for i in range(MIN_FIT + 10):
        long_out = ["target_first", "stop_first", "neither"][i % 3]
        db.insert("decisions", {"ts": i * 3600, "symbol": "X", "horizon_min": 30, "qset_version": "perp-v2.0", "decision_tf": "1",
                                "sl_bp": 50, "parsed_json": _parsed(0.6, 0.1, 0.1, 0.6),
                                "decision_json": {"meta": {"base_rates": base}},
                                "label_json": {"long": {"outcome": long_out}, "short": {"outcome": "stop_first"}, "return_bin": 3,
                                               "return_pct": 0.0, "max_up_pct": 0.1, "max_down_pct": -0.1}})
    w = Calibrator(db, tf_fn=lambda: "1").refit()
    assert w["long_bracket"] == w["short_bracket"]


# ---------------------------------------------------------------- policy
BASE = {"long_bracket": {"target_first": 0.25, "stop_first": 0.3, "neither": 0.45},
        "short_bracket": {"target_first": 0.25, "stop_first": 0.3, "neither": 0.45},
        "return_distribution": {f"bin_{i}": p for i, p in enumerate([0.02, 0.08, 0.25, 0.3, 0.25, 0.08, 0.02])},
        "price_higher_at_horizon": 0.5, "moved_at_least_one_stop_either_way": 0.5}
FULL = {q: {"model": 1.0} for q in ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")}


def _meta(**kw):
    m = {"sl_bp": 60.0, "tp_bp": 90.0, "cost_bp": 8.0, "price": 100.0, "base_rates": BASE, "events": ["volume_spike"],
         "validated": {"ok": True}, "flow_score": 0.5}
    m.update(kw)
    return m


def _strong_long():
    return _parsed(0.6, 0.15, 0.1, 0.55, up=0.7, mv=0.8)


def test_policy_v3_filters_and_gates():
    from ctb.policy import decide
    s = v3()
    d = decide(_strong_long(), _meta(), s, has_position=False, weights=FULL)
    assert d.action == "long" and d.model_side == "long" and not d.gates
    # no event on the bar: no trade, strict or explore
    s.exploration_mode = True
    d = decide(_strong_long(), _meta(events=[]), s, has_position=False, weights=FULL)
    assert d.action == "flat" and "no market event on this bar" in d.gate_kinds["filters"]
    # move unlikely
    weak_move = _strong_long()
    weak_move["big_move"]["p"] = 0.4
    d = decide(weak_move, _meta(), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("move unlikely" in g for g in d.gate_kinds["filters"])
    # liquidity
    d = decide(_strong_long(), _meta(mainnet_spread_bp=9.0), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("liquidity" in g for g in d.gate_kinds["hard"])
    d = decide(_strong_long(), _meta(depth10_usd=1000.0, planned_notional=5000.0), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("depth" in g for g in d.gate_kinds["hard"])


def test_policy_v3_validation_gate_is_strict_only():
    from ctb.policy import decide
    s = v3()
    meta = _meta(validated={"ok": False, "why": "undecided"})
    assert decide(_strong_long(), meta, s, has_position=False, weights=FULL).action == "flat"
    s.exploration_mode = True
    d = decide(_strong_long(), meta, s, has_position=False, weights=FULL)
    assert d.action == "long" and d.exploration


def test_policy_v3_direction_from_flow_and_agreement():
    from ctb.policy import decide
    s = v3()
    s.exploration_mode = True
    s.direction_source = "flow"
    d = decide(_strong_long(), _meta(flow_score=-0.6), s, has_position=False, weights=FULL)
    assert d.side_source == "flow" and d.model_side == "long" and d.action in ("short", "flat")
    d = decide(_strong_long(), _meta(flow_score=0.05), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("no direction" in g for g in d.gate_kinds["filters"])
    s.direction_source = "agree"
    d = decide(_strong_long(), _meta(flow_score=-0.6), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("disagrees" in g for g in d.gate_kinds["filters"])
    assert decide(_strong_long(), _meta(flow_score=0.6), s, has_position=False, weights=FULL).action == "long"


def test_policy_v3_drift_and_regime():
    from ctb.policy import decide
    s = v3()
    s.drift_action = "pause"
    s.exploration_mode = True
    d = decide(_strong_long(), _meta(drift={"alarm": True, "questions": ["long_bracket"]}), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("drift" in g for g in d.gate_kinds["hard"])
    s.drift_action = "base"
    s.allowed_regimes = ["trend_up/normal_vol"]
    d = decide(_strong_long(), _meta(regime="range/high_vol"), s, has_position=False, weights=FULL)
    assert d.action == "flat" and any("regime" in g for g in d.gate_kinds["hard"])


def test_dynamic_risk():
    from ctb.policy import risk_pct_for
    s = v3()
    base, _ = risk_pct_for(s, exploration=False, ev_r=s.min_edge_r)
    assert base == pytest.approx(s.risk_per_trade_pct)
    hi, _ = risk_pct_for(s, exploration=False, ev_r=10.0)
    assert hi == pytest.approx(1.5 * s.risk_per_trade_pct)  # capped
    cut, parts = risk_pct_for(s, exploration=False, ev_r=s.min_edge_r, vol_state="high", run_drawdown_pct=5.0, same_side_corr=[0.8, 0.7, -0.2])
    assert cut == pytest.approx(s.risk_per_trade_pct * 0.5 * 0.5 / 2.5) and parts["correlation"] == pytest.approx(0.4)
    s.dynamic_sizing = False
    assert risk_pct_for(s, exploration=True)[0] == pytest.approx(min(s.explore_risk_pct, s.risk_per_trade_pct))


# ---------------------------------------------------------------- validation
def test_page_hinkley_alarms_on_a_shift_only():
    from ctb.evaluation.validation import page_hinkley
    rng = random.Random(1)
    stable = [rng.gauss(0, 0.05) for _ in range(300)]
    shifted = stable[:150] + [x + 0.15 for x in stable[150:]]
    assert not page_hinkley(stable)["alarm"]
    res = page_hinkley(shifted)
    assert res["alarm"] and res["alarm_at_window"] > 150


def _labelled_decision(i, sym, informative, rng):
    up = rng.random() < 0.5
    out_long = "target_first" if up else "stop_first"
    out_short = "stop_first" if up else "target_first"
    guess = up if informative else rng.random() < 0.5  # an uninformative model is confident at random
    p_long = 0.7 if guess else 0.1
    p_short = 0.1 if guess else 0.7
    return {"id": i, "ts": i * 3600.0, "symbol": sym, "horizon_min": 30, "sl_bp": 50, "tp_bp": 75, "action": "flat",
            "parsed_json": _parsed(p_long, 0.2, p_short, 0.2, up=0.6 if up else 0.4),
            "decision_json": {"meta": {"base_rates": BASE, "costs": None, "cost_bp": 8.0, "hour_utc": i % 24, "events": ["volume_spike"]},
                              "ev_long_r": 0.0, "ev_short_r": 0.0, "model_side": "long" if p_long >= p_short else "short"},
            "label_json": {"long": {"outcome": out_long}, "short": {"outcome": out_short}, "return_bin": 5 if up else 1,
                           "return_pct": 0.6 if up else -0.6, "max_up_pct": 0.7 if up else 0.1, "max_down_pct": -0.1 if up else -0.7,
                           "long_r": 1.3 if up else -1.2, "short_r": -1.2 if up else 1.3}}


def test_skill_verdicts_and_walk_forward():
    from ctb.evaluation.validation import edge_vs_cost, skill_block, validated_symbols, walk_forward
    rng = random.Random(4)
    good = [_labelled_decision(i, "A", True, rng) for i in range(80)]
    bad = [_labelled_decision(i, "B", False, rng) for i in range(80)]
    assert skill_block("bracket", good)["verdict"] == "skill"
    assert skill_block("bracket", bad)["verdict"] == "no skill"
    v = validated_symbols({"A": {"bracket": skill_block("bracket", good)}, "B": {"bracket": skill_block("bracket", bad)}})
    assert v["A"]["ok"] and not v["B"]["ok"]
    wf = walk_forward(good, folds=4, min_edge_r=0.1)
    assert len(wf["folds"]) == 4 and wf["oos_bracket_skill"] > 0.1 and wf["oos_policy_mean_r"] > 0.5
    ec = edge_vs_cost(good, set())
    assert ec["model_side_all_bars"]["gross_edge_r"] > ec["model_side_all_bars"]["net_r"] > 0


def test_validation_compute_end_to_end(tmp_path):
    from ctb.evaluation import validation
    db = DB(tmp_path / "t.db")
    rng = random.Random(9)
    for i in range(70):
        d = _labelled_decision(i, "A" if i % 2 else "B", i % 2 == 1, rng)
        d.pop("id")
        db.insert("decisions", {**d, "qset_version": "perp-v2.0", "decision_tf": "1", "model_provider": "jev", "run_id": 1})
    v = validation.compute(db, model="jev", decision_tf="1")
    assert v["independent_windows"] == 70 and set(v["per_symbol"]) == {"A", "B"}
    assert {r["key"] for r in v["by_session"]} <= {"asia", "europe", "us", "late_us"}
    assert v["by_event"][0]["key"] == "volume_spike"
    assert "alarm" in v["drift"] and "execution" in v


# ---------------------------------------------------------------- labeller
def test_labeller_charges_per_outcome_cost_and_records_markouts():
    from ctb.evaluation.labeler import label_decision
    t0 = 1_700_000_000_000
    bars = [(t0 + 60000 * (i + 1), 100.0, 100.0 + (0.8 if i == 3 else 0.05), 99.95, 100.0 + 0.01 * i) for i in range(30)]
    costs = {"target_bp": 4.0, "stop_bp": 15.0, "time_bp": 9.5}
    d = {"ts": t0 / 1000, "horizon_min": 30, "price": 100.0, "sl_bp": 50, "tp_bp": 75, "decision_json": {"meta": {"costs": costs}}}
    lab = label_decision(d, bars)
    assert lab["long"]["outcome"] == "target_first"
    assert lab["long_r"] == pytest.approx(1.5 - 4.0 / 50)
    assert set(lab["markout_bp"]) == {"1", "5", "15"}


# ---------------------------------------------------------------- executor
class MakerClient:
    """Fake testnet: the post-only order fills after `fill_after` polls, or never."""

    def __init__(self, fill_after=2):
        self.fill_after, self.polls, self.cancelled, self.pos, self.posted = fill_after, 0, [], [], []

    async def orderbook(self, symbol, depth=50):
        return {"bids": [(99.99, 1e6)], "asks": [(100.01, 1e6)]}

    async def set_leverage(self, *a, **k):
        return None

    async def ticker(self, symbol):
        return {"markPrice": "100", "lastPrice": "100"}

    async def limit_order(self, symbol, side, qty, price, **k):
        if k.get("tif") == "PostOnly":
            self.posted.append((side, price, k))
            return {"orderId": "m1"}
        return {"orderId": "tp1"}

    async def order_history(self, symbol, order_link_id=None, order_id=None):
        self.polls += 1
        if self.fill_after is not None and self.polls >= self.fill_after:
            self.pos = [{"symbol": symbol, "side": "Buy", "size": "1", "avgPrice": "99.99", "stopLoss": "98", "takeProfit": ""}]
            return {"orderStatus": "Filled", "cumExecQty": "1", "avgPrice": "99.99", "cumExecFee": "0.002", "orderId": "m1"}
        if self.cancelled:
            return {"orderStatus": "Cancelled", "cumExecQty": "0", "avgPrice": "0", "orderId": "m1"}
        return {"orderStatus": "New", "cumExecQty": "0", "avgPrice": "0", "orderId": "m1"}

    async def cancel_order(self, symbol, oid):
        self.cancelled.append(oid)

    async def set_trading_stop(self, *a, **k):
        return None

    async def wallet(self):
        return {"equity": 100000.0}

    async def positions(self):
        return list(self.pos)

    async def open_orders(self):
        return []


def _maker_executor(tmp_path, client):
    from ctb.broker.executor import Executor
    s = v3()
    s.trading_enabled, s.entry_mode, s.maker_wait_s, s.max_open_risk_pct = True, "maker", 2, 50
    ex = Executor(s, DB(tmp_path / "t.db"), client, emit=lambda *a: None)
    ex.instruments = {"BTCUSDT": {"tick_size": 0.01, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 5, "max_qty": 1e9, "max_leverage": 10}}
    ex.wallet, ex.last_sync = {"equity": 100000.0}, time.time()
    return ex


def test_maker_entry_rests_on_own_side_and_records_sizing(tmp_path):
    client = MakerClient(fill_after=2)
    ex = _maker_executor(tmp_path, client)
    res = asyncio.run(ex.open(symbol="BTCUSDT", side="long", sl_bp=50, tp_bp=75, decision_id=1, ev_r=0.2, horizon_min=66, vol_state="normal"))
    assert res["ok"]
    side, price, k = client.posted[0]
    assert side == "buy" and price == "99.99" and k["tif"] == "PostOnly" and k["stop_loss"]  # bid, post-only, backstop attached
    t = ex.db.one("SELECT * FROM trades WHERE id=?", (res["trade_id"],))
    assert t["entry_mode"] == "maker" and t["horizon_min"] == 66 and t["entry_slip_bp"] == 0
    assert t["sizing_json"]["risk_pct"] == pytest.approx(ex.s.risk_per_trade_pct * 0.2 / ex.s.min_edge_r, rel=1e-3)
    assert t["deadline_ts"] - t["opened_ts"] == pytest.approx(66 * 60, abs=2)


def test_unfilled_maker_entry_is_cancelled(tmp_path):
    client = MakerClient(fill_after=None)
    ex = _maker_executor(tmp_path, client)
    res = asyncio.run(ex.open(symbol="BTCUSDT", side="long", sl_bp=50, tp_bp=75, decision_id=1, ev_r=0.2))
    assert not res["ok"] and client.cancelled == ["m1"]
    assert ex.db.one("SELECT status FROM trades")["status"] == "unfilled"


def test_break_even_moves_stop_and_keeps_original_risk(tmp_path):
    from ctb.broker.executor import Executor
    db = DB(tmp_path / "t.db")
    s = v3()
    s.be_trigger_r, s.be_offset_bp = 1.0, 5
    events, closes = [], []
    ex = Executor(s, db, client=None, emit=lambda k, d: events.append(d))
    tid = db.insert("trades", {"symbol": "BTCUSDT", "side": "long", "status": "open", "exit_mode": "managed", "qty": 1, "entry_price": 100.0,
                               "sl_price": 99.5, "tp_price": 100.75, "decision_price": 100.0, "planned_sl_bp": 50})
    ex.positions = [{"symbol": "BTCUSDT", "side": "Buy", "size": "1"}]
    tn, mn = {"bid1Price": "100.6"}, {"bid1Price": "100.6"}
    ex.price_fn, ex.mainnet_fn, ex.mainnet_age_fn = (lambda x: tn), (lambda x: mn), (lambda x: time.time())

    async def fake_close(symbol, reason, locked=False):
        closes.append(reason)
        return True
    ex.close = fake_close
    ex._refresh_managed()
    asyncio.run(ex.check_stop("BTCUSDT"))  # +1.2R on mainnet: move to break-even, no exit
    t = db.one("SELECT * FROM trades WHERE id=?", (tid,))
    assert t["be_moved_ts"] and t["sl_price"] == pytest.approx(100.05) and closes == []
    mn["bid1Price"] = tn["bid1Price"] = "100.04"  # back through break-even
    ex._refresh_managed()
    asyncio.run(ex.check_stop("BTCUSDT"))
    assert closes == ["stop_loss"]
