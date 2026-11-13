"""Turn raw market state into a compact, self-describing Kairos `state` object.

Design rules (driven by Kairos's 4096-token-per-question context):
- Only relative, unit-labelled numbers (percent, bp, ratios). Raw MA price levels are useless
  to a reader and cost tokens; "price is 0.42% above EMA50" is what a trader actually reads.
- Every field name says its unit. A short glossary is included for derived fields.
- The trade plan (exact stop/target distances, horizon, costs) is in state so every bracket
  question is concrete and its answer can be labelled against what really happened.
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from typing import Any

from . import indicators as ind
from .config import Settings
from .market.store import MarketStore, SymbolState

TF_LABEL = {"1": "1m", "5": "5m", "15": "15m", "60": "1h", "240": "4h"}


def _r(x: float | None, nd: int = 2) -> float | None:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    return round(x, nd)


def tf_block(st: SymbolState, tf: str) -> dict[str, Any] | None:
    bars = st.closed_bars(tf)
    if len(bars) < 60:
        return None
    c = [b.c for b in bars]
    h = [b.h for b in bars]
    l = [b.l for b in bars]
    v = [b.v for b in bars]
    last = c[-1]
    e20, e50, e200 = ind.ema(c, 20), ind.ema(c, 50), ind.ema(c, 200)
    m = ind.macd(c)
    a = ind.adx(h, l, c)
    atr = ind.atr(h, l, c)
    vol_avg = ind.sma(v[:-1], 20)
    return {
        "return_last_bar_pct": _r(ind.pct_change(c, 1), 3),
        "return_last_12_bars_pct": _r(ind.pct_change(c, 12), 3),
        "price_vs_ema20_pct": _r(100 * (last / e20 - 1), 3) if e20 else None,
        "price_vs_ema50_pct": _r(100 * (last / e50 - 1), 3) if e50 else None,
        "price_vs_ema200_pct": _r(100 * (last / e200 - 1), 3) if e200 else None,
        "ema20_vs_ema50_pct": _r(100 * (e20 / e50 - 1), 3) if e20 and e50 else None,
        "rsi14": _r(ind.rsi(c), 1),
        "adx14": _r(a[0], 1) if a else None,
        "di_plus_minus_di": _r(a[1] - a[2], 1) if a else None,
        "macd_hist_pct_of_price": _r(100 * m[2] / last, 4) if m else None,
        "atr14_pct": _r(100 * atr / last, 3) if atr else None,
        "bollinger_position_0_to_1": _r(ind.bollinger_pos(c), 2),
        "bollinger_width_pct": _r(ind.bollinger_width_pct(c), 3),
        "range_position_50_bars_0_to_1": _r(ind.range_position(h, l, last, 50), 2),
        "volume_vs_20bar_avg": _r(v[-1] / vol_avg, 2) if vol_avg else None,
    }


def atr_pct(st: SymbolState, tf: str) -> float | None:
    bars = st.closed_bars(tf)
    if len(bars) < 20:
        return None
    a = ind.atr([b.h for b in bars], [b.l for b in bars], [b.c for b in bars])
    return 100 * a / bars[-1].c if a else None


def bracket(st: SymbolState, s: Settings, models: Any = None, costs: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Stop/target distances in bp and the horizon in decision bars.

    atr mode: stop = sl_atr x ATR(atr_tf) clamped to [min_sl_bp, max_sl_bp]; fixed horizon.
    vol mode: stop = sl_sigma x GARCH sigma of the return over the horizon. The stop may not be so
      tight that the expected cost exceeds max_cost_r of it; when that floor binds and auto_horizon is on,
      the horizon is extended (up to max_horizon_mult x the profile horizon) until the stop is again
      sl_sigma standard deviations of the move over the horizon, so the target stays reachable."""
    a = atr_pct(st, s.atr_tf)
    if a is None:
        return None
    horizon = s.horizon_bars
    if s.bracket_mode == "vol" and models is not None:
        v = models.vol(st.symbol, s.decision_tf, horizon)
        if v:
            g = v["garch"]
            sig_h = v["sigma_horizon_bp"]
            stop = s.sl_sigma * sig_h
            ref_cost = (costs or {}).get("reference_bp", s.round_trip_cost_bp)
            floor = max(s.min_sl_bp_vol, ref_cost / s.max_cost_r if s.max_cost_r > 0 else 0.0)
            floor_bound = stop < floor
            if floor_bound:
                stop = floor
                if s.auto_horizon:
                    max_h = max(horizon, int(round(horizon * s.max_horizon_mult)))
                    lo, hi = horizon, max_h
                    if s.sl_sigma * g.horizon_sigma(hi) * 10000 < stop:
                        horizon = hi
                    else:
                        while lo < hi:  # smallest horizon whose sigma reaches the stop
                            mid = (lo + hi) // 2
                            if s.sl_sigma * g.horizon_sigma(mid) * 10000 >= stop:
                                hi = mid
                            else:
                                lo = mid + 1
                        horizon = lo
                    sig_h = g.horizon_sigma(horizon) * 10000
            stop = min(stop, s.max_sl_bp)
            tp = stop * s.reward_risk
            return {"sl_bp": round(stop, 1), "tp_bp": round(tp, 1), "atr_pct": a, "reward_risk": round(s.reward_risk, 3),
                    "horizon_bars": horizon, "sigma_horizon_bp": round(sig_h, 1), "mode": "vol", "cost_floor_bound": floor_bound,
                    "cost_floor_bp": round(floor, 1)}
    sl_bp = min(max(a * 100 * s.sl_atr, s.min_sl_bp), s.max_sl_bp)
    tp_bp = sl_bp * (s.tp_atr / s.sl_atr)
    return {"sl_bp": round(sl_bp, 1), "tp_bp": round(tp_bp, 1), "atr_pct": a, "reward_risk": round(tp_bp / sl_bp, 3),
            "horizon_bars": horizon, "mode": "atr", "cost_floor_bound": a * 100 * s.sl_atr < s.min_sl_bp}


BASE_RATE_MIN_WINDOWS = 60


BASE_RATE_MAX_WINDOWS = 600
CONDITIONAL_MIN_WINDOWS = 30


def base_rates(bars: list, horizon_bars: int, sl_bp: float, tp_bp: float, entries: set[int] | None = None,
               min_windows: int = BASE_RATE_MIN_WINDOWS) -> dict[str, Any] | None:
    """Empirical frequencies of every labelled outcome for THIS bracket and horizon on the
    instrument's own recent history (completed windows only: no look-ahead).

    Each closed bar is a hypothetical entry at its close; the next `horizon_bars` bars are the path.
    A bar touching target and stop together counts as stop first, as in the labeller. Laplace
    smoothing keeps every probability inside (0, 1). This is the forecast a model with no specific
    information should give, so it is both an anchor for Kairos and the baseline it must beat."""
    from .evaluation.labeler import bracket_outcome, return_bin

    last = len(bars) - horizon_bars - 1
    idx = [i for i in range(max(0, last - BASE_RATE_MAX_WINDOWS), last) if entries is None or i in entries]
    n_win = len(idx)
    if n_win < min_windows:
        return None
    cnt = {"long": {"target_first": 0, "stop_first": 0, "neither": 0}, "short": {"target_first": 0, "stop_first": 0, "neither": 0}}
    bins = [0] * 7
    up = move = 0
    d = sl_bp / 10000
    for i in idx:
        entry = bars[i].c
        path = [(b.t, b.o, b.h, b.l, b.c) for b in bars[i + 1: i + 1 + horizon_bars]]
        for side in ("long", "short"):
            cnt[side][bracket_outcome(path, entry, side, tp_bp, sl_bp)["outcome"]] += 1
        ret_pct = 100 * (path[-1][4] / entry - 1)
        bins[return_bin(ret_pct, sl_bp)] += 1
        up += ret_pct > 0
        move += max(b[2] for b in path) >= entry * (1 + d) or min(b[3] for b in path) <= entry * (1 - d)
    sm = lambda k, n, m: round((k + 1) / (n + m), 4)  # noqa: E731
    span_h = (bars[-1].t - bars[idx[0]].t) / 3_600_000
    return {
        "lookback_hours": round(span_h, 1),
        "windows": n_win,
        "long_bracket": {k: sm(v, n_win, 3) for k, v in cnt["long"].items()},
        "short_bracket": {k: sm(v, n_win, 3) for k, v in cnt["short"].items()},
        "return_distribution": {f"bin_{i}": sm(b, n_win, 7) for i, b in enumerate(bins)},
        "price_higher_at_horizon": sm(up, n_win, 2),
        "moved_at_least_one_stop_either_way": sm(move, n_win, 2),
    }


def _oi_change(st: SymbolState, minutes: int) -> float | None:
    h = st.oi_history
    if len(h) < 2:
        return None
    last_ts, last = h[-1]
    past = next((oi for ts, oi in reversed(h) if ts <= last_ts - minutes * 60000), None)
    return 100 * (last / past - 1) if past else None


def _ls(st: SymbolState) -> tuple[float | None, float | None]:
    if not st.ls_ratio:
        return None, None
    last = st.ls_ratio[-1]
    past = next((r for r in reversed(st.ls_ratio) if r[0] <= last[0] - 3600_000), None)
    return last[1], (last[1] - past[1]) if past else None


def book_block(st: SymbolState) -> dict[str, Any]:
    if not st.bids or not st.asks or time.time() - st.book_ts > 30:
        return {"available": False}
    bb, ba = st.bids[0][0], st.asks[0][0]
    mid = (bb + ba) / 2

    def depth(levels: list[tuple[float, float]], bp: float) -> float:
        lim = mid * bp / 10000
        return sum(p * q for p, q in levels if abs(p - mid) <= lim)

    b10, a10, b25, a25 = depth(st.bids, 10), depth(st.asks, 10), depth(st.bids, 25), depth(st.asks, 25)
    return {
        "spread_bp": _r(10000 * (ba - bb) / mid, 2),
        "bid_depth_within_10bp_usd": round(b10),
        "ask_depth_within_10bp_usd": round(a10),
        "imbalance_within_25bp": _r((b25 - a25) / (b25 + a25), 3) if b25 + a25 else None,
    }


def session_of(hour_utc: int) -> str:
    """Trading session by UTC hour (crypto trades 24/7; liquidity follows the big equity sessions)."""
    if 0 <= hour_utc < 7:
        return "asia"
    if 7 <= hour_utc < 13:
        return "europe"
    if 13 <= hour_utc < 21:
        return "us"
    return "late_us"


def flow_score(state: dict[str, Any]) -> float | None:
    """Direction implied by order flow alone, in [-1, 1]: the mean of the available signals
    (aggressor share, Hawkes intensity imbalance, book imbalance, queue model). Positive = buyers."""
    parts: list[float] = []
    f5 = (state.get("order_flow") or {}).get("last_5m") or {}
    if f5.get("taker_buy_share") is not None:
        parts.append(max(-1.0, min(1.0, (f5["taker_buy_share"] - 0.5) * 4)))
    fd = state.get("flow_dynamics") or {}
    if fd.get("intensity_imbalance_30s") is not None:
        parts.append(fd["intensity_imbalance_30s"])
    ob = state.get("order_book") or {}
    if ob.get("imbalance_within_25bp") is not None:
        parts.append(ob["imbalance_within_25bp"])
    q = ob.get("best_queue") or {}
    if q.get("p_next_mid_move_up") is not None:
        parts.append(max(-1.0, min(1.0, (q["p_next_mid_move_up"] - 0.5) * 4)))
    return round(sum(parts) / len(parts), 3) if parts else None


def build_state(store: MarketStore, symbol: str, s: Settings, *, position: dict[str, Any] | None = None, include_news: bool = True,
                models: Any = None, costs: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return (kairos_state, meta). meta holds numbers the policy/labeler need (not sent)."""
    st = store[symbol]
    dec = st.closed_bars(s.decision_tf)
    br = bracket(st, s, models, costs)
    if len(dec) < 60 or br is None:
        return None
    price = float(st.ticker.get("lastPrice") or dec[-1].c)
    tk = st.ticker

    def fnum(k: str) -> float | None:
        try:
            return float(tk[k])
        except (KeyError, TypeError, ValueError):
            return None

    mark, index, funding = fnum("markPrice"), fnum("indexPrice"), fnum("fundingRate")
    long_ratio, long_ratio_chg = _ls(st)
    f5, f15, f60 = st.flow_window(5), st.flow_window(15), st.flow_window(60)
    l15, l60 = st.liq_window(15), st.liq_window(60)
    horizon_bars = br["horizon_bars"]
    horizon_min = horizon_bars * s.decision_tf_minutes

    tfs = ("1", "5", "15", "60", "240") if s.decision_tf == "1" else ("5", "15", "60", "240")
    trend = {TF_LABEL[tf]: blk for tf in tfs if (blk := tf_block(st, tf))}

    cov = st.coverage_minutes()

    def flow(d: dict[str, float], minutes: int) -> dict[str, Any] | None:
        if cov < minutes:
            return None  # not enough stream history: unknown, not zero
        return {
            "taker_buy_share": _r(d["buy_share"], 3),
            "net_taker_flow_usd": round(d["cvd_usd"]),
            "large_trade_net_usd": round(d["large_buy_usd"] - d["large_sell_usd"]),
        }

    day = st.closed_bars("60")[-24:]
    state: dict[str, Any] = {
        "instrument": f"{symbol} USDT-margined perpetual swap",
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision_timeframe": TF_LABEL.get(s.decision_tf, s.decision_tf),
        "last_price": price,
        "change_24h_pct": _r(100 * float(tk["price24hPcnt"]), 2) if tk.get("price24hPcnt") else None,
        "range_position_24h_0_to_1": _r(ind.range_position([b.h for b in day], [b.l for b in day], price, len(day)), 2) if day else None,
        "trend_by_timeframe": trend,
        "order_flow": {"last_5m": flow(f5, 5), "last_15m": flow(f15, 15), "last_60m": flow(f60, 60), "stream_coverage_minutes": int(cov)},
        "derivatives": {
            "funding_rate_per_8h_pct": _r(100 * funding, 4) if funding is not None else None,
            "funding_avg_last_3_pct": _r(100 * sum(f for _, f in st.funding_history[-3:]) / 3, 4) if len(st.funding_history) >= 3 else None,
            "mark_minus_index_bp": _r(10000 * (mark / index - 1), 2) if mark and index else None,
            "open_interest_change_1h_pct": _r(_oi_change(st, 60), 3),
            "open_interest_change_4h_pct": _r(_oi_change(st, 240), 3),
            "accounts_long_share": _r(long_ratio, 3),
            "accounts_long_share_change_1h": _r(long_ratio_chg, 3),
            "long_liquidations_15m_usd": round(l15["long_liq_usd"]) if cov >= 15 else None,
            "short_liquidations_15m_usd": round(l15["short_liq_usd"]) if cov >= 15 else None,
            "long_liquidations_60m_usd": round(l60["long_liq_usd"]) if cov >= 60 else None,
            "short_liquidations_60m_usd": round(l60["short_liq_usd"]) if cov >= 60 else None,
        },
        "order_book": book_block(st),
    }
    now_s = time.time()
    fd = st.flow_intensity.features(now_s)
    if fd and cov >= 5:
        state["flow_dynamics"] = fd
    q = st.queue.features(now_s)
    if q and state["order_book"].get("available", True):
        state["order_book"]["best_queue"] = q
    events: list[str] = []
    vol = regime = None
    if models is not None:
        vol = models.vol(symbol, s.decision_tf, horizon_bars)
        regime = models.regime(symbol, s.decision_tf, horizon_bars)
        events = models.events(symbol, s.decision_tf, horizon_bars)
        if vol:
            state["volatility_forecast"] = {
                "model": "GARCH(1,1) on decision-timeframe log returns",
                "sigma_next_bar_pct": _r(vol["sigma_bar_bp"] / 100, 4),
                "sigma_over_horizon_pct": _r(vol["sigma_horizon_bp"] / 100, 3),
                "long_run_sigma_per_bar_pct": _r(vol["uncond_sigma_bar_bp"] / 100, 4),
                "vol_percentile_vs_recent_0_to_1": _r(vol["vol_percentile"], 2),
                "persistence_alpha_plus_beta": vol["persistence"],
            }
        state["regime"] = {k: regime[k] for k in ("regime", "trend", "vol", "efficiency_ratio")}
        if events:
            from .analytics import EVENT_TEXT
            state["market_events_on_last_bar"] = [{"event": e, "meaning": EVENT_TEXT[e]} for e in events]
    hour = datetime.now(timezone.utc).hour
    state["session"] = {"name": session_of(hour), "hour_utc": hour}

    # cross-market context
    cross: dict[str, Any] = {}
    if symbol != "BTCUSDT" and "BTCUSDT" in store.state:
        b = store["BTCUSDT"].closes("60")
        b5 = store["BTCUSDT"].closes("5")
        cross["btc_return_1h_pct"] = _r(ind.pct_change(b5, 12), 3)
        cross["btc_return_4h_pct"] = _r(ind.pct_change(b, 4), 3)
    fg = store.context.get("fear_greed")
    if fg:
        cross["fear_greed_index_0_100"] = fg["value"]
        cross["fear_greed_week_ago"] = fg["week_ago"]
    macro = store.context.get("macro") or {}
    for k in ("SPX_fut", "DXY", "VIX"):
        if k in macro:
            cross[f"{k}_change_8h_pct"] = macro[k]["chg_8h_pct"]
    if cross:
        state["cross_market"] = cross

    if include_news:
        now = time.time()
        rel = [n for n in store.context.get("news", [])
               if (symbol in n["symbols"] or n["market_wide"]) and now - (n.get("published_ts") or n["first_seen_ts"]) < 6 * 3600][:3]
        if rel:
            state["recent_headlines"] = [
                {"minutes_ago": int((now - (n.get("published_ts") or n["first_seen_ts"])) / 60), "source": n["source"], "title": n["title"][:160]}
                for n in rel
            ]

    if position:
        state["open_position"] = position

    base = base_rates(dec, horizon_bars, br["sl_bp"], br["tp_bp"])
    cond = None
    if models is not None:
        from .analytics import BAR_EVENTS
        now_bar_events = set(events) & set(BAR_EVENTS)
        if now_bar_events:
            hist = models.bar_event_history(symbol, s.decision_tf)
            entries = {i for i, b in enumerate(dec) if hist.get(b.t, set()) & now_bar_events}
            cond = base_rates(dec, horizon_bars, br["sl_bp"], br["tp_bp"], entries, min_windows=CONDITIONAL_MIN_WINDOWS)
            if cond:
                cond["given_events"] = sorted(now_bar_events)
    if base:
        state["historical_base_rates"] = {
            "what_this_is": ("How often each outcome actually happened for this exact bracket and horizon on this instrument "
                             f"over the last {base['lookback_hours']}h ({base['windows']} overlapping windows of {TF_LABEL.get(s.decision_tf, s.decision_tf)} bars). "
                             "A forecast without specific evidence should be close to these frequencies."),
            **{k: v for k, v in base.items() if k not in ("lookback_hours", "windows")},
        }

    if cond:
        state["historical_base_rates_after_same_events"] = {
            "what_this_is": (f"The same frequencies, counted only over past bars on which the same event happened "
                             f"({', '.join(cond['given_events'])}; {cond['windows']} windows). If this differs from the "
                             "unconditional base rates, the event has carried information on this instrument."),
            **{k: v for k, v in cond.items() if k not in ("lookback_hours", "windows", "given_events")},
        }

    plan = {
        "entry": ("limit order resting at the best bid (long) or ask (short) now" if s.entry_mode == "maker"
                  else "market order at last_price now"),
        "long_stop_pct_below_entry": round(br["sl_bp"] / 100, 3),
        "long_target_pct_above_entry": round(br["tp_bp"] / 100, 3),
        "short_stop_pct_above_entry": round(br["sl_bp"] / 100, 3),
        "short_target_pct_below_entry": round(br["tp_bp"] / 100, 3),
        "max_holding_minutes": horizon_min,
    }
    if costs:
        plan["cost_pct_if_target_hit"] = round(costs["target_bp"] / 100, 3)
        plan["cost_pct_if_stopped"] = round(costs["stop_bp"] / 100, 3)
        plan["cost_pct_if_closed_at_time_limit"] = round(costs["time_bp"] / 100, 3)
    else:
        plan["round_trip_cost_pct"] = round(s.round_trip_cost_bp / 100, 3)
    state["trade_plan"] = plan
    state["glossary"] = {
        "taker_buy_share": "fraction of aggressive (taker) volume that was buying; 0.5 = balanced",
        "net_taker_flow_usd": "taker buy notional minus taker sell notional",
        "accounts_long_share": "share of Bybit accounts net long",
        "di_plus_minus_di": "+DI minus -DI; positive = upward directional pressure",
        "long_liquidations": "forced closes of long positions (selling pressure)",
        "null": "value unknown (not enough data collected yet), not zero",
        "range_position": "0 = at the range low, 1 = at the high; values outside 0..1 mean price broke out of the range",
    }

    meta = {
        "symbol": symbol,
        "price": price,
        "testnet_price": float(st.testnet_ticker["lastPrice"]) if st.testnet_ticker.get("lastPrice") else None,
        "bar_time": dec[-1].t,
        "sl_bp": br["sl_bp"],
        "tp_bp": br["tp_bp"],
        "atr_pct": br["atr_pct"],
        "horizon_min": horizon_min,
        "horizon_bars": horizon_bars,
        "bracket": {k: v for k, v in br.items() if k not in ("sl_bp", "tp_bp", "atr_pct")},
        "cost_bp": costs["reference_bp"] if costs else s.round_trip_cost_bp,
        "costs": costs,
        "base_rates": base,
        "base_rates_given_events": cond,
        "events": events,
        "regime": (regime or {}).get("regime"),
        "vol_state": (regime or {}).get("vol"),
        "session": session_of(hour),
        "hour_utc": hour,
        "flow_score": flow_score(state),
    }
    if st.bids and st.asks and now_s - st.book_ts < 30:
        mid = (st.bids[0][0] + st.asks[0][0]) / 2
        meta["mainnet_spread_bp"] = round(10000 * (st.asks[0][0] - st.bids[0][0]) / mid, 3)
        meta["depth10_usd"] = min(state["order_book"].get("bid_depth_within_10bp_usd") or 0, state["order_book"].get("ask_depth_within_10bp_usd") or 0)
    tt = st.testnet_ticker
    try:
        tb, ta = float(tt["bid1Price"]), float(tt["ask1Price"])
        meta["testnet_spread_bp"] = round(10000 * (ta - tb) / ((ta + tb) / 2), 2) if ta > tb > 0 else None
    except (KeyError, TypeError, ValueError):
        meta["testnet_spread_bp"] = None
    return state, meta


# ---------------------------------------------------------------------------- mirror probe
# A mirrored state describes the same market upside-down: every bullish fact becomes the
# equivalent bearish fact. A coherent model should then swap its long and short answers.
_NEGATE = {
    "return_last_bar_pct", "return_last_12_bars_pct", "price_vs_ema20_pct", "price_vs_ema50_pct",
    "price_vs_ema200_pct", "ema20_vs_ema50_pct", "di_plus_minus_di", "macd_hist_pct_of_price",
    "change_24h_pct", "net_taker_flow_usd", "large_trade_net_usd", "funding_rate_per_8h_pct",
    "funding_avg_last_3_pct", "mark_minus_index_bp", "accounts_long_share_change_1h",
    "imbalance_within_25bp", "btc_return_1h_pct", "btc_return_4h_pct", "SPX_fut_change_8h_pct",
    "intensity_imbalance_5s", "intensity_imbalance_30s", "microprice_vs_mid_bp",
}
_HUNDRED_MINUS = {"rsi14", "fear_greed_index_0_100", "fear_greed_week_ago"}
_ONE_MINUS = {"bollinger_position_0_to_1", "range_position_50_bars_0_to_1", "range_position_24h_0_to_1", "taker_buy_share", "accounts_long_share",
              "best_queue_imbalance_0_to_1", "p_next_mid_move_up"}
_SWAP = [("long_liquidations_15m_usd", "short_liquidations_15m_usd"), ("long_liquidations_60m_usd", "short_liquidations_60m_usd"),
         ("bid_depth_within_10bp_usd", "ask_depth_within_10bp_usd"), ("buy_trades_per_s_5s", "sell_trades_per_s_5s"),
         ("buy_branching_ratio", "sell_branching_ratio"), ("buy_excitation_vs_baseline", "sell_excitation_vs_baseline")]
_EVENT_FLIP = {"range_break_up": "range_break_down", "range_break_down": "range_break_up", "vwap_cross_up": "vwap_cross_down",
               "vwap_cross_down": "vwap_cross_up", "flow_burst_buy": "flow_burst_sell", "flow_burst_sell": "flow_burst_buy"}


def _mirror_rates(hb: dict[str, Any]) -> dict[str, Any]:
    """The mirrored market's history: long and short swap, up and down swap."""
    return {
        **hb,
        "long_bracket": dict(hb["short_bracket"]), "short_bracket": dict(hb["long_bracket"]),
        "return_distribution": {f"bin_{i}": hb["return_distribution"][f"bin_{6 - i}"] for i in range(7)},
        "price_higher_at_horizon": round(1 - hb["price_higher_at_horizon"], 4),
    }


def mirror_state(state: dict[str, Any]) -> dict[str, Any]:
    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v is not None:
                    if k in _NEGATE:
                        v = -v
                    elif k in _HUNDRED_MINUS:
                        v = round(100 - v, 3)
                    elif k in _ONE_MINUS:
                        v = round(1 - v, 3)
                out[k] = walk(v) if isinstance(v, (dict, list)) else v
            for a, b in _SWAP:
                if a in out and b in out:
                    out[a], out[b] = out[b], out[a]
            return out
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    m = walk(state)
    for key in ("historical_base_rates", "historical_base_rates_after_same_events"):
        if state.get(key):
            m[key] = _mirror_rates(state[key])
    if state.get("regime"):
        flip = {"up": "down", "down": "up"}
        r = dict(m["regime"])
        r["trend"] = flip.get(r["trend"], r["trend"])
        r["regime"] = r["regime"].replace("trend_up", "trend_tmp").replace("trend_down", "trend_up").replace("trend_tmp", "trend_down")
        m["regime"] = r
    if state.get("market_events_on_last_bar"):
        from .analytics import EVENT_TEXT
        m["market_events_on_last_bar"] = [{"event": (e2 := _EVENT_FLIP.get(e["event"], e["event"])), "meaning": EVENT_TEXT[e2]}
                                          for e in state["market_events_on_last_bar"]]
        hb = m.get("historical_base_rates_after_same_events")
        if hb and "what_this_is" in hb:
            pat = re.compile("|".join(sorted(_EVENT_FLIP, key=len, reverse=True)))
            hb["what_this_is"] = pat.sub(lambda mt: _EVENT_FLIP[mt.group(0)], hb["what_this_is"])
    m.pop("recent_headlines", None)  # text cannot be mirrored
    m.pop("open_position", None)
    m["last_price"] = state["last_price"]
    return m
