import hashlib
import hmac
import math

import pytest

from ctb import indicators as ind
from ctb.config import Settings
from ctb.evaluation import metrics
from ctb.evaluation.labeler import bracket_outcome, label_decision, realized_r, return_bin
from ctb.features import mirror_state
from ctb.kairos import news
from ctb.kairos.parse import validate
from ctb.kairos.questions import build_questions
from ctb.policy import decide


def good_answers():
    return {
        "long_bracket": {"type": "choice", "choice": "target_first", "probabilities": {"target_first": 0.55, "stop_first": 0.30, "neither": 0.15}, "confidence": 0.4},
        "short_bracket": {"type": "choice", "choice": "stop_first", "probabilities": {"target_first": 0.25, "stop_first": 0.6, "neither": 0.15}, "confidence": 0.4},
        "return_distribution": {"type": "choice", "choice": "bin_4", "probabilities": {f"bin_{i}": p for i, p in enumerate([0.02, 0.05, 0.13, 0.2, 0.3, 0.2, 0.1])}, "confidence": 0.2},
        "setup_quality": {"type": "score", "score": 2.5, "legend": [], "probabilities": [0.0, 0.1, 0.4, 0.4, 0.1], "confidence": 0.3},
        "mean_reversion": {"type": "noul", "noul": 0.3},
        "direction": {"type": "noul", "noul": 0.62},
        "big_move": {"type": "noul", "noul": 0.7},
    }


Q = build_questions(60, 100, 120)
BASE = {"long_bracket": {"target_first": 0.2, "stop_first": 0.3, "neither": 0.5},
        "short_bracket": {"target_first": 0.2, "stop_first": 0.3, "neither": 0.5},
        "return_distribution": {f"bin_{i}": p for i, p in enumerate([0.02, 0.08, 0.25, 0.3, 0.25, 0.08, 0.02])},
        "price_higher_at_horizon": 0.5, "moved_at_least_one_stop_either_way": 0.55}
META = {"sl_bp": 60.0, "tp_bp": 100.0, "cost_bp": 15.0, "price": 100.0, "base_rates": BASE}
RAW = {q: 1.0 for q in ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")}  # trust Kairos fully


def v2_settings() -> Settings:
    """The v2 policy without the v3 filters (event trigger, move filter, validation gate), so the
    original policy tests keep testing what they were written for. v3 behaviour has its own tests."""
    s = Settings()
    s.trade_trigger, s.require_big_move, s.require_validated, s.symmetrize = "every_bar", False, False, False
    s.direction_source, s.allowed_regimes = "model", []
    return s


def test_questions_within_kairos_limits():
    assert 1 <= len(Q) <= 8
    for q in Q.values():
        if q["type"] == "choice":
            assert 2 <= len(q["criteria"]) <= 26
        if q["type"] == "score":
            assert 2 <= len(q["criteria"]) <= 10


def test_validate_accepts_contract_and_preserves_zero():
    parsed, issues = validate(good_answers(), Q)
    assert issues == []
    assert parsed["setup_quality"]["probs"][0] == 0.0
    assert set(parsed) == set(Q)


def test_validate_flags_bad_answers_without_repairing():
    a = good_answers()
    a["long_bracket"]["probabilities"]["neither"] = 0.6  # sum 1.45
    del a["direction"]
    a["mean_reversion"]["noul"] = float("nan")
    parsed, issues = validate(a, Q)
    assert "long_bracket" not in parsed and "direction" not in parsed and "mean_reversion" not in parsed
    assert any("sum" in i for i in issues) and any("missing answer" in i for i in issues)


def test_policy_ev_math_and_gates():
    parsed, _ = validate(good_answers(), Q)
    s = v2_settings()
    s.exploration_mode = False
    d = decide(parsed, META, s, has_position=False, weights=RAW)
    rr, cost_r = 100 / 60, 15 / 60
    assert d.ev_long_r == pytest.approx(round(0.55 * rr - 0.30 - cost_r, 4))
    assert d.ev_long_raw_r == d.ev_long_r
    assert d.action == "long" and d.strict_action == "long"
    d2 = decide(parsed, META, s, has_position=True, weights=RAW)
    assert d2.action == "flat" and "already in a position" in d2.reason


def test_policy_missing_answer_is_error_not_flat_judgement():
    parsed, _ = validate(good_answers(), Q)
    del parsed["long_bracket"]
    d = decide(parsed, META, Settings(), has_position=False)
    assert d.action == "flat" and "treated as error" in d.reason


def test_bracket_labels_and_ambiguity():
    bars = [(0, 100, 100.2, 99.9, 100.1), (60000, 100.1, 101.1, 100.0, 101.0)]
    assert bracket_outcome(bars, 100, "long", 100, 60)["outcome"] == "target_first"
    assert bracket_outcome(bars, 100, "short", 100, 60)["outcome"] == "stop_first"
    wide = [(0, 100, 101.5, 99.0, 100)]
    o = bracket_outcome(wide, 100, "long", 100, 60)
    assert o["outcome"] == "stop_first" and o["ambiguous"]
    assert realized_r("target_first", 100 / 60, 0, 60, "long", 15) == pytest.approx(100 / 60 - 0.25)
    assert return_bin(0.0, 60) == 3 and return_bin(-5, 60) == 0 and return_bin(5, 60) == 6


def test_label_decision_no_lookahead():
    ts = 1_000_000.5  # mid-minute
    start = (int(ts * 1000) // 60000 + 1) * 60000
    bars = [(start - 60000, 100, 200, 1, 100)]  # decision minute: huge range must be ignored
    bars += [(start + i * 60000, 100, 100.05, 99.95, 100) for i in range(120)]
    d = {"ts": ts, "price": 100.0, "sl_bp": 60.0, "tp_bp": 100.0, "horizon_min": 120, "decision_json": {"cost_bp": 15}, "state_json": {}}
    lab = label_decision(d, bars)
    assert lab["long"]["outcome"] == "neither" and lab["short"]["outcome"] == "neither"


def test_news_boundaries_follow_template_rule():
    body = news.build_request(news_text="x", target_asset="BTC", source_name="s", published_at_utc="2026-11-10T00:00:00Z", horizon="15m", round_trip_cost_bp=15)
    crit = body["questions"]["return_distribution"]["criteria"]
    assert crit["return_bin_0"] == "Gross asset return in [-infinity, -60) bp."
    assert crit["return_bin_3"] == "Gross asset return in [-15, 15) bp."
    assert crit["return_bin_6"] == "Gross asset return in [60, +infinity) bp."
    assert body["task_profile"] == "news_return" and body["model"] == "DMIND/Kairos"
    assert len(body["state"]) == 12
    with pytest.raises(ValueError):
        news.build_request(news_text="x", target_asset="BTC", source_name="s", published_at_utc="t", horizon="2h", round_trip_cost_bp=15)


def test_mirror_state_inverts_directional_fields():
    st = {"last_price": 100, "trend_by_timeframe": {"5m": {"rsi14": 70.0, "price_vs_ema50_pct": 0.5, "bollinger_position_0_to_1": 0.9}},
          "derivatives": {"long_liquidations_15m_usd": 10, "short_liquidations_15m_usd": 99, "accounts_long_share": 0.6},
          "recent_headlines": [{"title": "x"}]}
    m = mirror_state(st)
    b = m["trend_by_timeframe"]["5m"]
    assert b["rsi14"] == 30.0 and b["price_vs_ema50_pct"] == -0.5 and b["bollinger_position_0_to_1"] == pytest.approx(0.1)
    assert m["derivatives"]["long_liquidations_15m_usd"] == 99 and m["derivatives"]["accounts_long_share"] == pytest.approx(0.4)
    assert "recent_headlines" not in m
    assert mirror_state(m)["trend_by_timeframe"] == st["trend_by_timeframe"]


def test_indicators_sane():
    closes = [100 + math.sin(i / 5) * 2 + i * 0.05 for i in range(300)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    assert 0 <= ind.rsi(closes) <= 100
    assert ind.atr(highs, lows, closes) > 0
    a = ind.adx(highs, lows, closes)
    assert a and 0 <= a[0] <= 100
    assert ind.ema([1.0] * 50, 20) == pytest.approx(1.0)


def test_metrics_helpers():
    assert metrics.auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert metrics.auc([0.5, 0.5], [True, False]) == 0.5
    assert metrics._rps([0, 0, 1, 0], 2) == 0.0


def test_bybit_signature_matches_v5_spec():
    from ctb.broker.bybit_rest import RECV_WINDOW, BybitTestnet

    class _S: ...
    c = BybitTestnet("key", "secret", _S())  # type: ignore[arg-type]
    expected = hmac.new(b"secret", f"1700000000000key{RECV_WINDOW}category=linear".encode(), hashlib.sha256).hexdigest()
    assert c._sign("1700000000000", "category=linear") == expected
    assert c.host == "https://api-testnet.bybit.com"


def test_save_env_upserts_and_restricts_permissions(tmp_path, monkeypatch):
    import os
    import stat

    from ctb.config import key_hint, save_env

    env = tmp_path / ".env"
    env.write_text("# comment\nBYBIT_API_KEY=old\nCTB_SYMBOLS=BTCUSDT\n")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    save_env({"BYBIT_API_KEY": "newkey123", "KAIROS_API_KEY": "kk"}, path=env)
    text = env.read_text()
    assert "BYBIT_API_KEY=newkey123" in text and "old" not in text
    assert "# comment" in text and "CTB_SYMBOLS=BTCUSDT" in text and "KAIROS_API_KEY=kk" in text
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    with pytest.raises(ValueError):
        save_env({"KAIROS_API_KEY": "a\nEVIL=1"}, path=env)
    assert key_hint("abcdefgh1234") == {"set": True, "hint": "…1234"}
    assert key_hint("") == {"set": False, "hint": ""}


def _weak_answers(l_t, l_s, s_t, s_s):
    a = good_answers()
    a["long_bracket"]["probabilities"] = {"target_first": l_t, "stop_first": l_s, "neither": round(1 - l_t - l_s, 6)}
    a["long_bracket"]["choice"] = max(a["long_bracket"]["probabilities"], key=a["long_bracket"]["probabilities"].get)
    a["short_bracket"]["probabilities"] = {"target_first": s_t, "stop_first": s_s, "neither": round(1 - s_t - s_s, 6)}
    a["short_bracket"]["choice"] = max(a["short_bracket"]["probabilities"], key=a["short_bracket"]["probabilities"].get)
    a["setup_quality"].update(score=1.0, probabilities=[0.1, 0.8, 0.1, 0.0, 0.0])
    a["direction"]["noul"] = 0.5
    return a


def test_exploration_follows_lean_but_never_bypasses_safety():
    s = v2_settings()
    s.exploration_mode = True
    parsed, issues = validate(_weak_answers(0.36, 0.47, 0.29, 0.54), Q)  # real-like: long preferred
    assert issues == []
    d = decide(parsed, META, s, has_position=False)
    assert d.action == "long" and d.exploration and d.strict_action == "flat" and d.reason.startswith("EXPLORE")
    s.exploration_mode = False
    assert decide(parsed, META, s, has_position=False).action == "flat"
    s.exploration_mode = True
    assert decide(parsed, META, s, has_position=True).action == "flat"  # hard gate
    assert decide(parsed, {**META, "venue_divergence_bp": 400}, s, has_position=False).action == "flat"
    s.allow_short = False
    p2, _ = validate(_weak_answers(0.25, 0.55, 0.36, 0.45), Q)  # short preferred
    assert decide(p2, META, s, has_position=False).action == "flat"
    s.allow_short = True
    p3, _ = validate(_weak_answers(0.25, 0.52, 0.26, 0.49), Q)  # no meaningful lean
    d3 = decide(p3, META, s, has_position=False)
    assert d3.action == "flat" and any("lean" in g for g in d3.gates)


def test_exploration_sizing_uses_reduced_risk():
    from ctb.policy import size_position
    s = Settings()
    s.max_notional_usdt = 1e12
    full, _ = size_position(100_000, 50, 100, s)
    small, _ = size_position(100_000, 50, 100, s, risk_pct=0.1)
    assert small == pytest.approx(full * 0.1 / s.risk_per_trade_pct)


def test_tf_profiles_move_cadence_horizon_and_stop_together():
    from ctb.config import TF_PROFILES
    s = Settings()
    s.symbols = ["A", "B", "C", "D", "E"]
    s.symmetrize = False
    upd = s.apply_tf_profile("1")
    assert (s.decision_tf, s.horizon_bars, s.atr_tf, s.decision_tf_minutes) == ("1", 30, "5", 1)
    assert upd == {"CTB_DECISION_TF": "1", "CTB_HORIZON_BARS": "30", "CTB_ATR_TF": "5"}
    assert s.required_rph("1", probe_rate=0) == 300 and s.required_rph("5", probe_rate=0) == 60
    s.symmetrize = True  # every decision also asks the mirrored market
    assert s.required_rph("1", probe_rate=0) == 600
    s.apply_tf_profile("60")
    assert s.horizon_bars * s.decision_tf_minutes == 720
    for tf, p in TF_PROFILES.items():  # the stop is always sized from a slower timeframe than decisions
        assert int(p["atr_tf"]) > int(tf)


# ---------------------------------------------------------------- execution accuracy
from ctb.broker.executor import adverse_bp, aggregate_close_rows, depth_within, reason_from_orders  # noqa: E402


def _row(oid, size, exit_px, pnl, t, entry=0.06972729, of=0.0, cf=0.0):
    return {"orderId": oid, "closedSize": str(size), "avgEntryPrice": str(entry), "avgExitPrice": str(exit_px),
            "closedPnl": str(pnl), "openFee": str(of), "closeFee": str(cf), "createdTime": str(t), "updatedTime": str(t)}


def test_close_rows_are_summed_not_last_only():
    # The real DOGEUSDT exit of 10 Nov: one stop, three closed-P&L rows (Bybit export).
    rows = [_row("a", 8031, 0.0663, -28.12534774, 3, of=0.30798892, cf=0.29285042),
            _row("b", 51071, 0.06627118, -180.32648909, 1, of=1.9585732, cf=1.86149464),
            _row("c", 13006, 0.06673122, -39.94290897, 2, of=0.49878016, cf=0.47734846)]
    agg = aggregate_close_rows(rows, 72108)
    assert agg["complete"]
    assert abs(agg["pnl"] - (-248.39474580)) < 1e-6
    assert abs(agg["open_fee"] + agg["close_fee"] - 5.39703580) < 1e-6
    assert abs(agg["exit_price"] - 0.066358) < 1e-5
    assert len(agg["rows"]) == 3


def test_close_rows_incomplete_until_whole_size_posted():
    agg = aggregate_close_rows([_row("a", 16.2, 1.1, -0.37891555, 1)], 3380.3)
    assert agg and not agg["complete"]


def test_adverse_slippage_sign():
    assert adverse_bp("long", 100.10, 100.0, entry=True) == 10.0   # paid up
    assert adverse_bp("short", 99.90, 100.0, entry=True) == 10.0   # sold lower
    assert adverse_bp("long", 99.0, 100.0, entry=False) == 100.0   # stop filled below trigger
    assert adverse_bp("short", 101.0, 100.0, entry=False) == 100.0
    assert adverse_bp("long", 100.5, 100.0, entry=False) == -50.0  # better than trigger


def test_depth_within_cap():
    asks = [(100.0, 1.0), (100.05, 2.0), (100.2, 5.0)]
    assert depth_within(asks, 100.1, buy=True) == 3.0
    bids = [(99.9, 1.0), (99.8, 4.0)]
    assert depth_within(bids, 99.85, buy=False) == 1.0


def test_close_reason_from_order_create_type():
    assert reason_from_orders([{"createType": "CreateByStopLoss"}]) == "stop_loss"
    assert reason_from_orders([{"createType": "CreateByTakeProfit"}, {"createType": "CreateByTakeProfit"}]) == "take_profit"
    assert reason_from_orders([{"createType": "CreateByUser"}]) == "external"
    assert reason_from_orders([None]) is None


# ---------------------------------------------------------------- v2: base rates, calibration, policy
def strict() -> Settings:
    s = v2_settings()
    s.exploration_mode = False  # tests must not depend on the local .env
    return s

from ctb.calibration import blend, fit, rows_for  # noqa: E402
from ctb.features import base_rates  # noqa: E402
from ctb.market.store import Bar  # noqa: E402


def _bars(closes, spread=0.0):
    return [Bar(t=i * 300_000, o=c, h=c * (1 + spread), l=c * (1 - spread), c=c, v=1, turnover=1) for i, c in enumerate(closes)]


def test_base_rates_flat_market_is_all_neither():
    br = base_rates(_bars([100.0] * 200), 24, 60, 100)
    assert br["windows"] == 200 - 24 - 1
    assert br["long_bracket"]["neither"] > 0.98 and br["short_bracket"]["neither"] > 0.98
    assert br["moved_at_least_one_stop_either_way"] < 0.02
    assert abs(sum(br["return_distribution"].values()) - 1) < 1e-3


def test_base_rates_uptrend_and_no_lookahead():
    closes = [100 * (1.002 ** i) for i in range(200)]  # +0.2% per bar: long target (1%) hit in ~5 bars
    br = base_rates(_bars(closes), 24, 60, 100)
    assert br["long_bracket"]["target_first"] > 0.95 and br["short_bracket"]["stop_first"] > 0.95
    assert br["price_higher_at_horizon"] > 0.95
    # only completed windows: changing the last bar's future cannot exist, so dropping it changes n by 1
    assert base_rates(_bars(closes[:-1]), 24, 60, 100)["windows"] == br["windows"] - 1
    assert base_rates(_bars([100.0] * 50), 24, 60, 100) is None  # too little history


def test_blend_and_fit_recover_useless_and_perfect_models():
    assert blend({"a": 1.0, "b": 0.0}, {"a": 0.0, "b": 1.0}, 0.25) == pytest.approx({"a": 0.25, "b": 0.75})
    # a model that always says 90/10 while the truth is a coin flip and the base rate knows it -> w = 0
    useless = [([0.9, 0.1], [0.5, 0.5], i % 2) for i in range(200)]
    assert fit(useless)["w"] == 0.0 and fit(useless)["fitted"]
    # a model that is right every time -> w = 1
    perfect = [([1.0, 0.0] if i % 2 == 0 else [0.0, 1.0], [0.5, 0.5], i % 2) for i in range(200)]
    assert fit(perfect)["w"] == 1.0
    assert fit(perfect[:20])["w"] == 0.5 and not fit(perfect[:20])["fitted"]  # prior until MIN_FIT independent windows


def test_policy_v2_shrinks_overconfident_targets_to_base_rates():
    parsed, _ = validate(good_answers(), Q)
    s = strict()
    raw = decide(parsed, META, s, has_position=False, weights=RAW)
    base_only = decide(parsed, META, s, has_position=False, weights={q: 0.0 for q in RAW})
    assert raw.action == "long"
    assert base_only.ev_long_r == base_only.ev_long_base_r  # pure base rates
    assert base_only.action == "flat" and any("min edge" in g for g in base_only.gates)


def test_policy_v2_requires_direction_agreement_and_base_rates():
    a = good_answers()
    a["direction"]["noul"] = 0.40  # bracket says long, direction says down
    parsed, _ = validate(a, Q)
    d = decide(parsed, META, strict(), has_position=False, weights=RAW)
    assert d.action == "flat" and any("direction" in g for g in d.gates)
    parsed, _ = validate(good_answers(), Q)
    d = decide(parsed, {k: v for k, v in META.items() if k != "base_rates"}, strict(), has_position=False, weights=RAW)
    assert d.action == "flat" and any("base rates" in g for g in d.gates)


def test_calibration_rows_label_direction_and_big_move():
    dec = {"parsed_json": {"direction": {"p": 0.7}, "big_move": {"p": 0.4}}, "sl_bp": 50,
           "decision_json": {"meta": {"base_rates": BASE}},
           "label_json": {"return_pct": 0.2, "max_up_pct": 0.6, "max_down_pct": -0.1}}
    assert rows_for("direction", [dec]) == [([[0.7, pytest.approx(0.3)]], [0.5, 0.5], 0)]
    assert rows_for("big_move", [dec])[0][2] == 0  # +0.6% >= 0.5% stop -> moved


def test_mirror_swaps_base_rates():
    st = {"last_price": 1, "historical_base_rates": {"what_this_is": "x", **BASE,
          "long_bracket": {"target_first": 0.1, "stop_first": 0.6, "neither": 0.3}, "price_higher_at_horizon": 0.4}}
    m = mirror_state(st)["historical_base_rates"]
    assert m["short_bracket"]["stop_first"] == 0.6 and m["price_higher_at_horizon"] == 0.6
    assert m["return_distribution"]["bin_0"] == BASE["return_distribution"]["bin_6"]


def test_runs_stamp_history_and_rotate(tmp_path):
    from ctb import runs
    from ctb.db import DB
    db = DB(tmp_path / "t.db")
    for t in (100, 200, 300):
        db.insert("decisions", {"ts": t, "symbol": "BTCUSDT"})
        db.insert("trades", {"symbol": "BTCUSDT", "side": "long", "opened_ts": t, "status": "closed"})
    runs.define_history(db, [("Run 1", 0, 150, {}, ""), ("Run 2", 150, None, {}, "")])
    assert [r["run_id"] for r in db.query("SELECT run_id FROM decisions ORDER BY ts")] == [1, 2, 2]
    assert runs.current(db)["name"] == "Run 2"
    r3 = runs.start(db, strict())
    assert r3["id"] == 3 and r3["name"].startswith("Run 3") and r3["config_json"]["exploration_mode"] is False
    assert db.one("SELECT ended_ts FROM runs WHERE id=2")["ended_ts"] is not None
    assert runs.ensure(db, strict())["id"] == 3  # a restart keeps the open run


def test_managed_stop_needs_mainnet_confirmation(tmp_path):
    """10 Nov: testnet XRP fell 4.4% in a minute while mainnet rose; a stop must not sell that hole."""
    import asyncio
    import time as _t
    from ctb.broker.executor import Executor
    from ctb.db import DB
    db = DB(tmp_path / "t.db")
    events, closes = [], []
    ex = Executor(strict(), db, client=None, emit=lambda k, d: events.append(d))
    tid = db.insert("trades", {"symbol": "XRPUSDT", "side": "long", "status": "open", "exit_mode": "managed", "qty": 1,
                               "sl_price": 1.0471, "tp_price": 1.0816, "decision_price": 1.0597, "planned_sl_bp": 120})
    ex.positions = [{"symbol": "XRPUSDT", "side": "Buy", "size": "1"}]
    tn, mn = {"bid1Price": "1.0200"}, {"bid1Price": "1.0640"}
    ex.price_fn, ex.mainnet_fn = (lambda s: tn), (lambda s: mn)
    ex.mainnet_age_fn = lambda s: _t.time()

    async def fake_close(symbol, reason, locked=False):
        closes.append(reason)
        return True
    ex.close = fake_close
    ex._refresh_managed()
    asyncio.run(ex.check_stop("XRPUSDT"))
    assert closes == [] and any("dislocation" in (e.get("kind") or "") for e in events)  # testnet only: hold
    mn["bid1Price"] = "1.0460"  # mainnet stop = 1.0597 * (1 - 1.2%) = 1.0470
    asyncio.run(ex.check_stop("XRPUSDT"))
    asyncio.run(ex.check_stop("XRPUSDT"))
    assert closes == ["stop_loss"]  # fires once
    assert db.one("SELECT stop_trigger_px FROM trades WHERE id=?", (tid,))["stop_trigger_px"] == 1.046
    # stale mainnet data falls back to the testnet price so protection never lapses
    ex._stop_fired.clear()
    closes.clear()
    mn["bid1Price"] = "1.0640"
    ex.mainnet_age_fn = lambda s: _t.time() - 60
    asyncio.run(ex.check_stop("XRPUSDT"))
    assert closes == ["stop_loss"]



def test_calibration_is_fitted_per_trading_timeframe(tmp_path):
    from ctb.calibration import MIN_FIT, PRIOR_W, Calibrator
    from ctb.db import DB
    db = DB(tmp_path / "t.db")
    for i in range(MIN_FIT + 20):  # a useless 5m model: says 90% up, truth alternates, base rate knows 50/50
        db.insert("decisions", {"ts": i, "symbol": "BTCUSDT", "qset_version": "perp-v2.0", "decision_tf": "5", "sl_bp": 50,
                                "parsed_json": {"direction": {"p": 0.9}}, "decision_json": {"meta": {"base_rates": BASE}},
                                "label_json": {"return_pct": 0.1 if i % 2 else -0.1, "max_up_pct": 0, "max_down_pct": 0}})
    tf = {"v": "5"}
    cal = Calibrator(db, tf_fn=lambda: tf["v"])
    assert cal.refit()["direction"] == {"model": 0.0}  # fitted: ignore the useless model on 5m
    tf["v"] = "1"
    assert cal.maybe_refit()["direction"] == {"model": PRIOR_W}  # 1m has no history of its own: prior, not 5m's weight


# ---------------------------------------------------------------- multi-model (Kairos + Jev)
def _jev_answers(questions):
    """Answers shaped like TypeSafe's documented response (score probabilities keyed by level strings)."""
    out = {}
    for qid, q in questions.items():
        if q["type"] == "noul":
            out[qid] = {"type": "noul", "noul": 0.61}
        elif q["type"] == "choice":
            opts = list(q["criteria"])
            p = {o: round(1 / len(opts), 6) for o in opts}
            p[opts[0]] = round(1 - sum(v for k, v in p.items() if k != opts[0]), 6)
            out[qid] = {"type": "choice", "choice": max(p, key=p.get), "confidence": 0.3, "probabilities": p}
        else:
            n = len(q["criteria"])
            probs = {str(i): (1.0 if i == 1 else 0.0) for i in range(n)}
            out[qid] = {"type": "score", "score": 1.0, "confidence": 0.5, "probabilities": probs,
                        "legend": {str(i): c for i, c in enumerate(q["criteria"])}}
    return out


def test_jev_client_retries_overload_and_answers_validate(monkeypatch):
    import asyncio
    import json as _json
    import aiohttp
    from aiohttp import web as aweb
    from ctb.kairos.client import SystemOneClient
    from ctb.models import MODELS

    seen = []

    async def handler(req):
        body = await req.json()
        seen.append({"auth": req.headers.get("Authorization"), "body": body})
        if len(seen) == 1:
            return aweb.json_response({"detail": "overloaded"}, status=529)
        return aweb.json_response({"model": "jev-1.14.0", "answers": _jev_answers(body["questions"]),
                                   "usage": {"input_tokens": 1234, "output_tokens": 70}})

    async def run():
        app = aweb.Application()
        app.router.add_post("/v1/systemone", handler)
        runner = aweb.AppRunner(app)
        await runner.setup()
        site = aweb.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test-key-123456")
        async with aiohttp.ClientSession() as session:
            client = SystemOneClient(MODELS["jev"], session, base_url=f"http://127.0.0.1:{port}")
            qs = {**Q, "extra_1": {"type": "noul", "instructions": "x"}, "extra_2": {"type": "noul", "instructions": "y"}}
            res = await client.ask({"last_price": 1}, qs)  # 9 questions: fine for Jev (no 8-question cap)
            parsed, issues = validate(res.answers, Q | {"extra_1": qs["extra_1"], "extra_2": qs["extra_2"]})
        await runner.cleanup()
        return res, parsed, issues

    import asyncio as _a
    from unittest import mock
    with mock.patch("asyncio.sleep", new=lambda *_a, **_k: _noop()):
        res, parsed, issues = asyncio.run(run())
    assert res.attempts == 2  # 529 retried once
    assert seen[1]["auth"] == "Bearer ts-test-key-123456"
    assert seen[1]["body"]["model"] == "jev-1.14.0" and "task_profile" not in seen[1]["body"]
    assert issues == [] and parsed["setup_quality"]["score"] == 1.0 and parsed["direction"]["p"] == 0.61
    assert res.usage["input_tokens"] == 1234 and res.metadata["model"] == "jev-1.14.0"


async def _noop():
    return None


def test_kairos_keeps_its_question_cap_and_news_profile_is_kairos_only():
    import asyncio
    import aiohttp
    from ctb.kairos.client import KairosError, SystemOneClient
    from ctb.models import MODELS

    async def run():
        async with aiohttp.ClientSession() as session:
            k = SystemOneClient(MODELS["kairos"], session, api_key_getter=lambda: "k")
            with pytest.raises(KairosError, match="1-8 questions"):
                await k.ask({}, {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(9)})
            j = SystemOneClient(MODELS["jev"], session, api_key_getter=lambda: "k")
            with pytest.raises(KairosError, match="task_profile"):
                await j.ask({}, {"q": {"type": "noul", "instructions": "x"}}, task_profile="news_return")
    asyncio.run(run())


def test_calibration_is_fitted_per_model(tmp_path):
    from ctb.calibration import MIN_FIT, PRIOR_W, Calibrator
    from ctb.db import DB
    db = DB(tmp_path / "t.db")
    for i in range(MIN_FIT + 20):
        db.insert("decisions", {"ts": i, "symbol": "BTCUSDT", "qset_version": "perp-v2.0", "decision_tf": "5", "sl_bp": 50,
                                "model_provider": "kairos", "parsed_json": {"direction": {"p": 0.9}},
                                "decision_json": {"meta": {"base_rates": BASE}},
                                "label_json": {"return_pct": 0.1 if i % 2 else -0.1, "max_up_pct": 0, "max_down_pct": 0}})
    prov = {"v": "kairos"}
    cal = Calibrator(db, tf_fn=lambda: "5", provider_fn=lambda: prov["v"])
    assert cal.refit()["direction"] == {"model": 0.0}
    prov["v"] = "jev"
    assert cal.maybe_refit()["direction"] == {"model": PRIOR_W}  # Jev starts from the prior, never from Kairos's weights


def test_concurrent_entries_respect_same_side_cap(tmp_path):
    """Fast models answer every symbol at once; caps must hold when entries run concurrently."""
    import asyncio
    import time as _t
    from ctb.broker.executor import Executor
    from ctb.db import DB

    class FakeClient:
        def __init__(self):
            self.pos = []

        async def orderbook(self, symbol, depth=50):
            await asyncio.sleep(0.01)
            return {"bids": [(99.99, 1e6)], "asks": [(100.0, 1e6)]}

        async def set_leverage(self, *a, **k):
            return None

        async def ticker(self, symbol):
            return {"markPrice": "100", "lastPrice": "100"}

        async def market_order(self, symbol, side, qty, **k):
            await asyncio.sleep(0.05)  # fills land after the other entries already checked the caps
            self.pos.append({"symbol": symbol, "side": "Sell", "size": qty, "avgPrice": "99.99", "stopLoss": "101", "takeProfit": ""})
            return {"orderId": f"o-{symbol}"}

        async def order_history(self, symbol, order_link_id=None, order_id=None):
            return {"orderStatus": "Filled", "cumExecQty": "1", "avgPrice": "99.99", "cumExecFee": "0.01", "orderId": f"o-{symbol}"}

        async def set_trading_stop(self, *a, **k):
            return None

        async def limit_order(self, *a, **k):
            return {"orderId": "tp"}

        async def wallet(self):
            return {"equity": 100000.0}

        async def positions(self):
            return list(self.pos)

        async def open_orders(self):
            return []

    s = strict()
    s.trading_enabled, s.max_same_side, s.max_positions, s.max_open_risk_pct = True, 4, 10, 50
    ex = Executor(s, DB(tmp_path / "t.db"), FakeClient(), emit=lambda *a: None)
    syms = [f"S{i}USDT" for i in range(6)]
    ex.instruments = {sym: {"tick_size": 0.01, "qty_step": 0.001, "min_qty": 0.001, "min_notional": 5, "max_qty": 1e9, "max_leverage": 10} for sym in syms}
    ex.wallet, ex.last_sync = {"equity": 100000.0}, _t.time()

    async def run():
        return await asyncio.gather(*(ex.open(symbol=sym, side="short", sl_bp=60, tp_bp=100, decision_id=i, ev_r=0.0, exploration=True)
                                      for i, sym in enumerate(syms)))
    res = asyncio.run(run())
    assert sum(1 for r in res if r.get("ok")) == 4
    assert all("max same side" in r["reason"] for r in res if not r.get("ok"))
