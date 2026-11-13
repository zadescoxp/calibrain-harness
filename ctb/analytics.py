"""Per-symbol market models kept warm by the engine: GARCH volatility, regime, correlations,
Hawkes refits, and detection of the market events that can trigger a trade.

Event detection is split in two:
- bar events are computed from closed decision-timeframe bars only, so they can be replayed over the
  bar history to measure base rates *conditional on the same event* (no look-ahead);
- stream events need the live trade/liquidation stream and are known only from now on.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any

from .market.store import Bar, MarketStore, SymbolState
from .quant import Garch, FlowIntensity, classify_regime, correlation, fit_garch, log_returns, percentile_rank, rolling_sigma

log = logging.getLogger("ctb.analytics")

EVENT_LOOKBACK = 60  # bars of history each bar event is measured against
VWAP_BARS = 240
BAR_EVENTS = ("volume_spike", "range_break_up", "range_break_down", "vwap_cross_up", "vwap_cross_down", "big_bar")
STREAM_EVENTS = ("flow_burst_buy", "flow_burst_sell", "cvd_divergence", "liquidation_burst", "flow_excited", "vol_jump")
EVENT_TEXT = {
    "volume_spike": "volume of the last bar is at least 3 standard deviations above its 60-bar average",
    "range_break_up": "the last bar closed above the highest high of the previous 60 bars",
    "range_break_down": "the last bar closed below the lowest low of the previous 60 bars",
    "vwap_cross_up": "price crossed above the rolling 4h VWAP on the last bar",
    "vwap_cross_down": "price crossed below the rolling 4h VWAP on the last bar",
    "big_bar": "the last bar's return is at least 3 times the recent per-bar volatility",
    "flow_burst_buy": "net aggressive buying in the last minute is at least 3 standard deviations above the past hour",
    "flow_burst_sell": "net aggressive selling in the last minute is at least 3 standard deviations above the past hour",
    "cvd_divergence": "over 15 minutes, aggressive flow and price moved in opposite directions",
    "liquidation_burst": "liquidations in the last minute are at least 5 times the typical minute of the past hour",
    "flow_excited": "order-flow intensity is at least 3 times its Hawkes baseline",
    "vol_jump": "GARCH next-bar volatility is at least twice its long-run level",
}


def bar_events_at(bars: list[Bar], i: int) -> set[str]:
    """Events on bar i, measured only against bars before it."""
    if i < EVENT_LOOKBACK + 1:
        return set()
    out: set[str] = set()
    prev = bars[i - EVENT_LOOKBACK:i]
    b = bars[i]
    vols = [x.v for x in prev]
    mv = sum(vols) / len(vols)
    sv = math.sqrt(sum((v - mv) ** 2 for v in vols) / len(vols))
    if sv > 0 and (b.v - mv) / sv >= 3:
        out.add("volume_spike")
    if b.c > max(x.h for x in prev):
        out.add("range_break_up")
    elif b.c < min(x.l for x in prev):
        out.add("range_break_down")
    rets = [math.log(prev[k].c / prev[k - 1].c) for k in range(1, len(prev)) if prev[k - 1].c > 0]
    if rets and prev[-1].c > 0:
        sd = math.sqrt(sum(r * r for r in rets) / len(rets))
        r = math.log(b.c / prev[-1].c)
        if sd > 0 and abs(r) >= 3 * sd:
            out.add("big_bar")
    w = bars[max(0, i - VWAP_BARS):i]
    vol = sum(x.v for x in w)
    if vol > 0:
        vw = sum(x.turnover for x in w) / vol if any(x.turnover for x in w) else sum((x.h + x.l + x.c) / 3 * x.v for x in w) / vol
        if prev[-1].c <= vw < b.c:
            out.add("vwap_cross_up")
        elif prev[-1].c >= vw > b.c:
            out.add("vwap_cross_down")
    return out


def stream_events(st: SymbolState, garch: Garch | None, now_ms: int | None = None) -> set[str]:
    now_ms = now_ms or int(time.time() * 1000)
    out: set[str] = set()
    if st.coverage_minutes(now_ms) >= 30:
        mf = st.minute_flow(61, now_ms)
        if len(mf) >= 31:
            hist = [x for _, x in mf[:-1]]
            m = sum(hist) / len(hist)
            sd = math.sqrt(sum((x - m) ** 2 for x in hist) / len(hist))
            last = mf[-1][1]
            if sd > 0 and (last - m) / sd >= 3:
                out.add("flow_burst_buy")
            elif sd > 0 and (m - last) / sd >= 3:
                out.add("flow_burst_sell")
        f15 = st.flow_window(15, now_ms)
        c15 = [b.c for b in st.closed_bars("1")[-16:]]
        if f15["buy_share"] is not None and len(c15) >= 16 and c15[0] > 0:
            ret = c15[-1] / c15[0] - 1
            lean = f15["buy_share"] - 0.5
            if abs(lean) >= 0.06 and abs(ret) >= 0.001 and (lean > 0) != (ret > 0):
                out.add("cvd_divergence")
        per_min: dict[int, float] = {}
        for ts, _side, notional in st.liquidations:
            if ts >= now_ms - 61 * 60000:
                per_min[ts - ts % 60000] = per_min.get(ts - ts % 60000, 0.0) + notional
        current = now_ms - now_ms % 60000
        last_min = per_min.get(current - 60000, 0.0)
        hist = sorted(v for k, v in per_min.items() if k < current - 60000 and v > 0)
        typical = hist[len(hist) // 2] if hist else 0.0
        if last_min >= 25_000 and last_min >= 5 * max(typical, 1.0):
            out.add("liquidation_burst")
    fi = st.flow_intensity
    for side in ("Buy", "Sell"):
        fit = fi.fits.get(side)
        if fit and fit.mu > 0 and fi.intensity(side, 30.0, now_ms / 1000) / fit.mu >= 3:
            out.add("flow_excited")
    if garch and garch.uncond_var > 0 and math.sqrt(garch.var_last / garch.uncond_var) >= 2:
        out.add("vol_jump")
    return out


class MarketModels:
    GARCH_REFIT_S = 900
    CORR_REFIT_S = 600
    HAWKES_REFIT_S = 300

    def __init__(self, store: MarketStore):
        self.store = store
        self.garch: dict[tuple[str, str], Garch] = {}
        self.corr: dict[tuple[str, str], float] = {}
        self.corr_at = 0.0
        self.hawkes_at = 0.0
        self._bar_events: dict[tuple[str, str], dict[int, set[str]]] = {}

    # ------------------------------------------------------------------ volatility
    def garch_for(self, symbol: str, tf: str) -> Garch | None:
        key = (symbol, tf)
        g = self.garch.get(key)
        bars = self.store[symbol].closed_bars(tf)
        if g is None or time.time() - g.fitted_at > self.GARCH_REFIT_S:
            fitted = fit_garch(log_returns([b.c for b in bars]))
            if fitted and bars:
                fitted.last_t = bars[-1].t
                self.garch[key] = g = fitted
        if g is None:
            return None
        # roll the conditional variance forward over the bars that closed since the fit
        new = [b for b in bars if b.t > g.last_t]
        prev = next((b.c for b in reversed(bars) if b.t <= g.last_t), None)
        v = g.var_last
        for b in new:
            if prev and prev > 0 and b.c > 0:
                x = math.log(b.c / prev)
                v = g.omega + g.alpha * x * x + g.beta * v
            prev = b.c
        return Garch(g.omega, g.alpha, g.beta, v, g.loglik, g.n, g.uncond_var, g.fitted_at, bars[-1].t if bars else g.last_t)

    def vol(self, symbol: str, tf: str, horizon_bars: int) -> dict[str, Any] | None:
        g = self.garch_for(symbol, tf)
        if not g:
            return None
        rets = log_returns(self.store[symbol].closes(tf))
        hist = rolling_sigma(rets, 30)
        sig_bar = math.sqrt(g.var_last)
        return {
            "sigma_bar_bp": sig_bar * 10000,
            "sigma_horizon_bp": g.horizon_sigma(horizon_bars) * 10000,
            "uncond_sigma_bar_bp": math.sqrt(g.uncond_var) * 10000,
            "vol_percentile": percentile_rank(hist, sig_bar),
            "alpha": g.alpha, "beta": g.beta, "persistence": round(g.persistence, 4),
            "garch": g,
        }

    def regime(self, symbol: str, tf: str, horizon_bars: int) -> dict[str, Any]:
        v = self.vol(symbol, tf, horizon_bars)
        closes = self.store[symbol].closes(tf)[-60:]
        return classify_regime(v["vol_percentile"] if v else None, closes)

    # ------------------------------------------------------------------ events
    def bar_event_history(self, symbol: str, tf: str) -> dict[int, set[str]]:
        """Bar events for every closed bar (keyed by open time), computed once per bar and cached."""
        cache = self._bar_events.setdefault((symbol, tf), {})
        bars = self.store[symbol].closed_bars(tf)
        for i, b in enumerate(bars):
            if b.t not in cache and i >= EVENT_LOOKBACK + 1:
                cache[b.t] = bar_events_at(bars, i)
        if len(cache) > 2 * len(bars) + 100:
            keep = {b.t for b in bars}
            for t in [t for t in cache if t not in keep]:
                del cache[t]
        return cache

    def events(self, symbol: str, tf: str, horizon_bars: int) -> list[str]:
        bars = self.store[symbol].closed_bars(tf)
        ev = bar_events_at(bars, len(bars) - 1) if bars else set()
        v = self.vol(symbol, tf, horizon_bars)
        ev |= stream_events(self.store[symbol], v["garch"] if v else None)
        return sorted(ev)

    # ------------------------------------------------------------------ correlation
    def correlations(self, symbols: list[str]) -> dict[tuple[str, str], float]:
        if time.time() - self.corr_at < self.CORR_REFIT_S and self.corr:
            return self.corr
        rets = {s: log_returns(self.store[s].closes("5")[-145:]) for s in symbols if s in self.store.state}
        out: dict[tuple[str, str], float] = {}
        for a in rets:
            for b in rets:
                if a < b:
                    c = correlation(rets[a], rets[b])
                    if c is not None:
                        out[(a, b)] = out[(b, a)] = round(c, 3)
        self.corr, self.corr_at = out, time.time()
        return out

    def corr_with(self, symbol: str, others: list[str]) -> dict[str, float]:
        c = self.correlations(self.store.symbols)
        return {o: c[(symbol, o)] for o in others if (symbol, o) in c}

    # ------------------------------------------------------------------ background refits
    async def loop(self) -> None:
        """Hawkes fits are the only expensive part (profile likelihood over a few thousand events),
        so they run in a worker thread on a snapshot taken here, never on the event loop."""
        while True:
            try:
                if time.time() - self.hawkes_at >= self.HAWKES_REFIT_S:
                    self.hawkes_at = time.time()
                    for sym in list(self.store.symbols):
                        fi: FlowIntensity = self.store[sym].flow_intensity
                        snap = fi.snapshot()
                        fi.fits = await asyncio.to_thread(FlowIntensity.fit_snapshot, snap)
                        fi.fitted_at = time.time()
            except Exception as exc:  # noqa: BLE001
                log.warning("hawkes refit: %s", exc)
            await asyncio.sleep(30)
