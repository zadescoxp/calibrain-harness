"""Quantitative market models used to size brackets, describe the market and gate trades.

Everything here is plain Python (no numpy): the inputs are a few hundred bars or a few thousand
trades per symbol, refits run every few minutes, and the harness stays dependency-light.

- GARCH(1,1): volatility forecast over the trade horizon. Sizes the bracket and feeds the regime.
- Hawkes process (exponential kernel): self-exciting order-flow intensity per side. The branching
  ratio n says how much of the flow is triggered by earlier flow (n -> 1: a cascade) versus arriving
  on its own; the live intensity against the fitted baseline says whether flow is excited right now.
- Queue model: the best-level queue imbalance and an online estimate of P(next mid move is up | imbalance),
  learned from this symbol's own book (a reduced queue-reactive model).
- Regime: volatility state (GARCH sigma against its own recent history) x trend state (efficiency ratio).
- Correlation of 5m returns between symbols, for portfolio risk.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------- GARCH(1,1)


@dataclass
class Garch:
    omega: float
    alpha: float
    beta: float
    var_last: float  # conditional variance for the next step
    loglik: float
    n: int
    uncond_var: float
    fitted_at: float = field(default_factory=time.time)
    last_t: int = 0  # open time (ms) of the last bar the fit has seen

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    def forecast_var(self, steps: int) -> list[float]:
        """Per-step conditional variance for the next `steps` steps."""
        out, v = [], self.var_last
        p, vbar = self.persistence, self.uncond_var
        for _ in range(max(1, steps)):
            out.append(v)
            v = vbar + p * (v - vbar)  # E[sigma^2_{t+k+1}] mean-reverts to the unconditional variance
        return out

    def horizon_sigma(self, steps: int) -> float:
        """Standard deviation of the summed log return over `steps` steps."""
        return math.sqrt(sum(self.forecast_var(steps)))


def log_returns(closes: list[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]


def _garch_ll(r: list[float], alpha: float, beta: float, var0: float) -> tuple[float, float]:
    """Gaussian log-likelihood with variance targeting (omega = var0 * (1 - alpha - beta))."""
    omega = var0 * (1 - alpha - beta)
    v, ll = var0, 0.0
    for x in r:
        if v <= 0:
            return -1e18, v
        ll -= 0.5 * (math.log(v) + x * x / v)
        v = omega + alpha * x * x + beta * v
    return ll, v


def fit_garch(returns: list[float]) -> Garch | None:
    """Maximum likelihood with variance targeting: a coarse grid over (alpha, beta), then a local
    refinement. Variance targeting pins omega to the sample variance, which keeps the fit stable on
    a few hundred returns. Returns None when there is too little data."""
    r = [x for x in returns if math.isfinite(x)]
    if len(r) < 100:
        return None
    mean = sum(r) / len(r)
    r = [x - mean for x in r]
    var0 = sum(x * x for x in r) / len(r)
    if var0 <= 0:
        return None
    best = (-1e18, 0.05, 0.9, var0)
    for a in (0.02, 0.05, 0.08, 0.12, 0.16, 0.2, 0.25, 0.3):
        for b in (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.93, 0.96):
            if a + b >= 0.995:
                continue
            ll, v = _garch_ll(r, a, b, var0)
            if ll > best[0]:
                best = (ll, a, b, v)
    step = 0.02
    for _ in range(4):  # local refinement around the grid optimum
        improved = False
        for da, db in ((step, 0), (-step, 0), (0, step), (0, -step), (step, -step), (-step, step)):
            a, b = best[1] + da, best[2] + db
            if a <= 0.001 or b <= 0 or a + b >= 0.995:
                continue
            ll, v = _garch_ll(r, a, b, var0)
            if ll > best[0]:
                best, improved = (ll, a, b, v), True
        if not improved:
            step /= 2
    ll, a, b, v = best
    return Garch(omega=var0 * (1 - a - b), alpha=round(a, 4), beta=round(b, 4), var_last=v, loglik=ll, n=len(r), uncond_var=var0)


def rolling_sigma(returns: list[float], window: int) -> list[float]:
    out = []
    for i in range(window, len(returns) + 1):
        w = returns[i - window:i]
        m = sum(w) / window
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / window))
    return out


def percentile_rank(values: list[float], x: float) -> float | None:
    if not values:
        return None
    return sum(1 for v in values if v <= x) / len(values)


# ---------------------------------------------------------------------------- Hawkes


@dataclass
class HawkesFit:
    mu: float  # baseline events per second
    branching: float  # n = alpha / beta: share of events triggered by earlier events
    beta: float  # kernel decay per second
    loglik: float
    n_events: int
    span_s: float


def hawkes_loglik(times: list[float], mu: float, alpha: float, beta: float) -> float:
    """Exact log-likelihood of a univariate Hawkes process with kernel alpha * exp(-beta * t),
    observed on [times[0], times[-1]]. O(n) with the usual recursion."""
    if mu <= 0 or alpha < 0 or beta <= 0:
        return -1e18
    t0, T = times[0], times[-1]
    ll, a_rec, prev = 0.0, 0.0, None
    for t in times:
        if prev is not None:
            a_rec = math.exp(-beta * (t - prev)) * (1 + a_rec)
        lam = mu + alpha * a_rec
        ll += math.log(lam)
        prev = t
    comp = mu * (T - t0) + (alpha / beta) * sum(1 - math.exp(-beta * (T - t)) for t in times)
    return ll - comp


def fit_hawkes(times: list[float], betas: tuple[float, ...] = (0.05, 0.2, 1.0, 5.0)) -> HawkesFit | None:
    """Profile maximum likelihood: for each kernel decay beta and branching ratio n on a grid, the
    baseline mu is pinned by the mean rate (mu = (1 - n) * N / T), so only (beta, n) are searched.
    Timestamps in seconds, sorted. Duplicate timestamps (one aggressive order filling several
    resting orders) are merged first: they are one decision by one trader, not several arrivals."""
    ts: list[float] = []
    for t in times:
        if not ts or t - ts[-1] > 1e-3:
            ts.append(t)
    if len(ts) < 200:
        return None
    span = ts[-1] - ts[0]
    if span <= 0:
        return None
    rate = len(ts) / span
    best: HawkesFit | None = None
    for beta in betas:
        for n in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95):
            mu = (1 - n) * rate
            ll = hawkes_loglik(ts, mu, n * beta, beta)
            if best is None or ll > best.loglik:
                best = HawkesFit(mu=mu, branching=n, beta=beta, loglik=ll, n_events=len(ts), span_s=span)
    return best


class FlowIntensity:
    """Live order-flow intensity per aggressor side, plus a periodically refitted Hawkes model.

    intensity(side, tau) is an exponentially decayed count of trades (per second) with time constant
    tau: the empirical counterpart of the Hawkes intensity. Comparing it with the fitted baseline mu
    says whether flow on that side is excited right now."""

    TAUS = (5.0, 30.0)
    MAX_EVENTS = 3000
    REFIT_S = 300
    WINDOW_S = 1800

    def __init__(self) -> None:
        self._lam = {side: {tau: 0.0 for tau in self.TAUS} for side in ("Buy", "Sell")}
        self._last = {"Buy": 0.0, "Sell": 0.0}
        self.events: dict[str, deque] = {"Buy": deque(maxlen=self.MAX_EVENTS), "Sell": deque(maxlen=self.MAX_EVENTS)}
        self.fits: dict[str, HawkesFit | None] = {"Buy": None, "Sell": None}
        self.fitted_at = 0.0

    def add(self, ts_s: float, side: str) -> None:
        side = "Buy" if side == "Buy" else "Sell"
        ev = self.events[side]
        if ev and ts_s - ev[-1] <= 1e-3:
            return  # same aggressive order filling several resting orders
        last = self._last[side]
        for tau in self.TAUS:
            decay = math.exp(-(ts_s - last) / tau) if last else 0.0
            self._lam[side][tau] = self._lam[side][tau] * decay + 1.0 / tau
        self._last[side] = ts_s
        ev.append(ts_s)

    def intensity(self, side: str, tau: float, now_s: float | None = None) -> float:
        now_s = now_s or time.time()
        last = self._last[side]
        if not last:
            return 0.0
        return self._lam[side][tau] * math.exp(-max(0.0, now_s - last) / tau)

    def snapshot(self, now_s: float | None = None) -> dict[str, list[float]]:
        """Copy of recent event times, taken on the event loop so a fit can run in a worker thread."""
        now_s = now_s or time.time()
        return {side: [t for t in list(self.events[side]) if t >= now_s - self.WINDOW_S] for side in ("Buy", "Sell")}

    @staticmethod
    def fit_snapshot(snap: dict[str, list[float]]) -> dict[str, HawkesFit | None]:
        return {side: fit_hawkes(ts) for side, ts in snap.items()}

    def refit(self, now_s: float | None = None) -> None:
        """Synchronous refit (tests and scripts); the engine refits in a worker thread instead."""
        self.fits = self.fit_snapshot(self.snapshot(now_s))
        self.fitted_at = now_s or time.time()

    def features(self, now_s: float | None = None) -> dict[str, Any] | None:
        now_s = now_s or time.time()
        b5, s5 = self.intensity("Buy", 5.0, now_s), self.intensity("Sell", 5.0, now_s)
        b30, s30 = self.intensity("Buy", 30.0, now_s), self.intensity("Sell", 30.0, now_s)
        if b30 + s30 <= 0:
            return None
        fb, fs = self.fits["Buy"], self.fits["Sell"]
        out: dict[str, Any] = {
            "buy_trades_per_s_5s": round(b5, 3), "sell_trades_per_s_5s": round(s5, 3),
            "intensity_imbalance_5s": round((b5 - s5) / (b5 + s5), 3) if b5 + s5 else None,
            "intensity_imbalance_30s": round((b30 - s30) / (b30 + s30), 3),
        }
        if fb and fs:
            out.update({
                "buy_branching_ratio": fb.branching, "sell_branching_ratio": fs.branching,
                "buy_excitation_vs_baseline": round(b30 / fb.mu, 2) if fb.mu else None,
                "sell_excitation_vs_baseline": round(s30 / fs.mu, 2) if fs.mu else None,
            })
        return out


# ---------------------------------------------------------------------------- queue model


class QueueModel:
    """Best-level queue imbalance I = q_bid / (q_bid + q_ask) and an online estimate of
    P(next mid-price move is up | I bucket), learned from the symbol's own book updates.

    This is the empirical core of queue-reactive models (Cont & de Larrard; Huang, Lehalle &
    Rosenbaum): the side with the thinner best queue is the one more likely to be depleted, so the
    mid tends to move toward it. Counts decay so the estimate tracks the current book regime."""

    BUCKETS = 5
    DECAY = 0.999

    def __init__(self) -> None:
        self.up = [1.0] * self.BUCKETS  # Laplace prior: 50/50 per bucket
        self.down = [1.0] * self.BUCKETS
        self.last_mid: float | None = None
        self.last_bucket: int | None = None
        self.imbalance: float | None = None
        self.microprice_offset_bp: float | None = None
        self.mid_changes: deque = deque(maxlen=600)  # timestamps of mid changes

    @classmethod
    def bucket(cls, imb: float) -> int:
        return min(int(imb * cls.BUCKETS), cls.BUCKETS - 1)

    def update(self, best_bid: tuple[float, float], best_ask: tuple[float, float], now_s: float | None = None) -> None:
        (pb, qb), (pa, qa) = best_bid, best_ask
        if qb + qa <= 0 or pa <= pb:
            return
        mid = (pb + pa) / 2
        if self.last_mid is not None and mid != self.last_mid and self.last_bucket is not None:
            k = self.last_bucket
            for i in range(self.BUCKETS):
                self.up[i] *= self.DECAY
                self.down[i] *= self.DECAY
            if mid > self.last_mid:
                self.up[k] += 1
            else:
                self.down[k] += 1
            self.mid_changes.append(now_s or time.time())
        imb = qb / (qb + qa)
        self.imbalance = imb
        self.last_bucket = self.bucket(imb)
        self.last_mid = mid
        micro = (pb * qa + pa * qb) / (qa + qb)
        self.microprice_offset_bp = (micro / mid - 1) * 10000

    def p_up(self, imb: float | None = None) -> float | None:
        imb = self.imbalance if imb is None else imb
        if imb is None:
            return None
        k = self.bucket(imb)
        return self.up[k] / (self.up[k] + self.down[k])

    def observations(self) -> int:
        return int(round(sum(self.up) + sum(self.down) - 2 * self.BUCKETS))

    def features(self, now_s: float | None = None) -> dict[str, Any] | None:
        if self.imbalance is None:
            return None
        now_s = now_s or time.time()
        return {
            "best_queue_imbalance_0_to_1": round(self.imbalance, 3),
            "p_next_mid_move_up": round(self.p_up(), 3),
            "learned_from_mid_moves": self.observations(),
            "mid_moves_last_minute": sum(1 for t in self.mid_changes if t >= now_s - 60),
            "microprice_vs_mid_bp": round(self.microprice_offset_bp, 3) if self.microprice_offset_bp is not None else None,
        }


# ---------------------------------------------------------------------------- regime


def efficiency_ratio(closes: list[float]) -> float | None:
    """|net move| / sum of |bar moves|: 1 = a straight line, ~0 = back and forth."""
    if len(closes) < 3:
        return None
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path else 0.0


def classify_regime(vol_pct_rank: float | None, closes: list[float], er_trend: float = 0.3) -> dict[str, Any]:
    """vol state from the GARCH sigma's percentile against the symbol's own recent rolling sigma;
    trend state from the efficiency ratio and direction of the last `len(closes)` bars."""
    er = efficiency_ratio(closes)
    if vol_pct_rank is None:
        vol = "unknown"
    elif vol_pct_rank >= 0.8:
        vol = "high"
    elif vol_pct_rank <= 0.2:
        vol = "low"
    else:
        vol = "normal"
    if er is None:
        trend = "unknown"
    elif er >= er_trend:
        trend = "up" if closes[-1] > closes[0] else "down"
    else:
        trend = "range"
    name = f"{'trend_' + trend if trend in ('up', 'down') else trend}/{vol}_vol"
    return {"regime": name, "trend": trend, "vol": vol, "efficiency_ratio": round(er, 3) if er is not None else None,
            "vol_percentile": round(vol_pct_rank, 3) if vol_pct_rank is not None else None}


# ---------------------------------------------------------------------------- correlation


def correlation(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None
