"""In-memory market state per symbol, fed by the Bybit public websocket and REST pollers."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..quant import FlowIntensity, QueueModel

TIMEFRAMES = ["1", "5", "15", "60", "240"]
MAX_BARS = 1000


@dataclass
class Bar:
    t: int  # open time, ms
    o: float
    h: float
    l: float
    c: float
    v: float
    turnover: float
    closed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"time": self.t // 1000, "open": self.o, "high": self.h, "low": self.l, "close": self.c, "volume": self.v}


@dataclass
class SymbolState:
    symbol: str
    bars: dict[str, list[Bar]] = field(default_factory=lambda: {tf: [] for tf in TIMEFRAMES})
    ticker: dict[str, Any] = field(default_factory=dict)
    testnet_ticker: dict[str, Any] = field(default_factory=dict)
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    book_ts: float = 0.0
    # taker flow per minute bucket: minute_ms -> [buy_qty, sell_qty, buy_notional, sell_notional, n, large_buy_notional, large_sell_notional]
    flow: dict[int, list[float]] = field(default_factory=dict)
    liquidations: deque = field(default_factory=lambda: deque(maxlen=2000))  # (ts_ms, pos_side, notional)
    large_trades: deque = field(default_factory=lambda: deque(maxlen=300))  # (ts_ms, taker_side, qty, price, notional)
    oi_history: list[tuple[int, float]] = field(default_factory=list)  # (ts_ms, oi contracts), 5min
    ls_ratio: list[tuple[int, float, float]] = field(default_factory=list)  # (ts_ms, buy_ratio, sell_ratio)
    funding_history: list[tuple[int, float]] = field(default_factory=list)
    instrument: dict[str, Any] = field(default_factory=dict)  # testnet lot/tick filters
    flow_intensity: FlowIntensity = field(default_factory=FlowIntensity)  # Hawkes-style order-flow intensity
    queue: QueueModel = field(default_factory=QueueModel)  # best-level queue imbalance model
    stream_since_ms: int = 0  # continuous trade/liquidation stream coverage starts here (reset on reconnect)
    last_update: float = 0.0

    def closes(self, tf: str) -> list[float]:
        return [b.c for b in self.bars[tf] if b.closed]

    def closed_bars(self, tf: str) -> list[Bar]:
        return [b for b in self.bars[tf] if b.closed]

    def upsert_bar(self, tf: str, bar: Bar) -> bool:
        """Insert or update a bar. Returns True when a bar transitioned to closed."""
        series = self.bars[tf]
        if series and series[-1].t == bar.t:
            was_closed = series[-1].closed
            series[-1] = bar
            return bar.closed and not was_closed
        if series and bar.t < series[-1].t:
            # out-of-order update for an older bar: patch in place if present
            for i in range(len(series) - 1, -1, -1):
                if series[i].t == bar.t:
                    series[i] = bar
                    break
            return False
        series.append(bar)
        if len(series) > MAX_BARS:
            del series[: len(series) - MAX_BARS]
        return bar.closed

    def coverage_minutes(self, now_ms: int | None = None) -> float:
        if not self.stream_since_ms:
            return 0.0
        return ((now_ms or int(time.time() * 1000)) - self.stream_since_ms) / 60000

    def add_trade(self, ts_ms: int, side: str, qty: float, price: float, large_threshold: float) -> None:
        minute = ts_ms - ts_ms % 60000
        b = self.flow.setdefault(minute, [0.0] * 7)
        notional = qty * price
        self.flow_intensity.add(ts_ms / 1000, side)
        if notional >= large_threshold:
            self.large_trades.append((ts_ms, side, qty, price, notional))
        if side == "Buy":
            b[0] += qty
            b[2] += notional
            if notional >= large_threshold:
                b[5] += notional
        else:
            b[1] += qty
            b[3] += notional
            if notional >= large_threshold:
                b[6] += notional
        b[4] += 1
        if len(self.flow) > 300:
            for k in sorted(self.flow)[:-240]:
                del self.flow[k]

    def minute_flow(self, minutes: int, now_ms: int | None = None) -> list[tuple[int, float]]:
        """Net taker flow (buy - sell notional) per completed minute, oldest first."""
        now_ms = now_ms or int(time.time() * 1000)
        current = now_ms - now_ms % 60000
        return [(m, self.flow[m][2] - self.flow[m][3]) for m in sorted(self.flow) if current - minutes * 60000 <= m < current]

    def flow_window(self, minutes: int, now_ms: int | None = None) -> dict[str, float]:
        now_ms = now_ms or int(time.time() * 1000)
        cutoff = now_ms - minutes * 60000
        buy = sell = n = lb = ls = 0.0
        for minute, b in self.flow.items():
            if minute >= cutoff:
                buy += b[2]
                sell += b[3]
                n += b[4]
                lb += b[5]
                ls += b[6]
        total = buy + sell
        return {
            "taker_buy_usd": buy,
            "taker_sell_usd": sell,
            "cvd_usd": buy - sell,
            "buy_share": buy / total if total else None,
            "trades": n,
            "large_buy_usd": lb,
            "large_sell_usd": ls,
        }

    def liq_window(self, minutes: int, now_ms: int | None = None) -> dict[str, float]:
        now_ms = now_ms or int(time.time() * 1000)
        cutoff = now_ms - minutes * 60000
        longs = shorts = 0.0
        for ts, side, notional in self.liquidations:
            if ts >= cutoff:
                if side == "Buy":  # Bybit: a Buy update means a LONG position was liquidated
                    longs += notional
                else:
                    shorts += notional
        return {"long_liq_usd": longs, "short_liq_usd": shorts}


class MarketStore:
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.state: dict[str, SymbolState] = {s: SymbolState(s) for s in symbols}
        self.context: dict[str, Any] = {"fear_greed": None, "macro": {}, "news": []}
        self.ws_connected = False
        self.ws_messages = 0
        self.ws_last_msg = 0.0
        self.data_errors: deque = deque(maxlen=200)

    def __getitem__(self, symbol: str) -> SymbolState:
        return self.state[symbol]

    def record_error(self, source: str, err: str) -> None:
        self.data_errors.append({"ts": time.time(), "source": source, "error": err[:300]})
