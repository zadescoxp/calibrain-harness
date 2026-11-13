"""Bybit public market data (mainnet, no keys) + testnet ticker for venue divergence.

Signals are computed from MAINNET data because testnet order flow, OI, funding and
liquidations are synthetic. Orders go to TESTNET, whose prices drift from mainnet, so the
executor places brackets relative to the testnet fill and refuses to trade when the two
venues diverge too far.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import aiohttp

from .store import TIMEFRAMES, Bar, MarketStore

log = logging.getLogger("ctb.market")

MAINNET_REST = "https://api.bybit.com"
TESTNET_REST = "https://api-testnet.bybit.com"
MAINNET_WS = "wss://stream.bybit.com/v5/public/linear"

# Notional above which a single taker print counts as a "large" trade, per symbol.
LARGE_TRADE_USD = {"BTCUSDT": 250_000, "ETHUSDT": 150_000}
DEFAULT_LARGE_TRADE_USD = 50_000

BarCloseCallback = Callable[[str, str], Awaitable[None]]
WS_SHARD = 8  # symbols per public WebSocket connection
UNIVERSE_SIZE = 100  # top USDT perps by 24h turnover offered in the symbol picker


class BybitPublicFeed:
    def __init__(self, store: MarketStore, session: aiohttp.ClientSession):
        self.store = store
        self.session = session
        self.on_bar_close: list[BarCloseCallback] = []
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ REST helpers
    async def _get(self, base: str, path: str, params: dict) -> dict:
        async with self.session.get(f"{base}{path}", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
        if int(data.get("retCode", -1)) != 0:
            raise RuntimeError(f"{path} {data.get('retCode')} {data.get('retMsg')}")
        return data["result"]

    async def warmup(self) -> None:
        sem = asyncio.Semaphore(6)  # 8 REST calls per symbol; stay far below Bybit's public limits

        async def one(sym: str) -> None:
            async with sem:
                await self._warm_symbol(sym)
        await asyncio.gather(*(one(s) for s in self.store.symbols))

    async def _warm_symbol(self, symbol: str) -> None:
        st = self.store[symbol]
        for tf in TIMEFRAMES:
            try:
                res = await self._get(MAINNET_REST, "/v5/market/kline", {"category": "linear", "symbol": symbol, "interval": tf, "limit": 1000})
                rows = sorted(res["list"], key=lambda r: int(r[0]))
                now_ms = int(time.time() * 1000)
                tf_ms = int(tf) * 60000
                st.bars[tf] = [
                    Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]),
                        closed=int(r[0]) + tf_ms <= now_ms)
                    for r in rows
                ]
            except Exception as exc:  # noqa: BLE001
                self.store.record_error(f"kline:{symbol}:{tf}", str(exc))
        await self._poll_derivs(symbol)

    async def _poll_derivs(self, symbol: str) -> None:
        st = self.store[symbol]
        try:
            res = await self._get(MAINNET_REST, "/v5/market/open-interest", {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 100})
            st.oi_history = sorted((int(r["timestamp"]), float(r["openInterest"])) for r in res["list"])
        except Exception as exc:  # noqa: BLE001
            self.store.record_error(f"oi:{symbol}", str(exc))
        try:
            res = await self._get(MAINNET_REST, "/v5/market/account-ratio", {"category": "linear", "symbol": symbol, "period": "5min", "limit": 50})
            st.ls_ratio = sorted((int(r["timestamp"]), float(r["buyRatio"]), float(r["sellRatio"])) for r in res["list"])
        except Exception as exc:  # noqa: BLE001
            self.store.record_error(f"ls_ratio:{symbol}", str(exc))
        try:
            res = await self._get(MAINNET_REST, "/v5/market/funding/history", {"category": "linear", "symbol": symbol, "limit": 30})
            st.funding_history = sorted((int(r["fundingRateTimestamp"]), float(r["fundingRate"])) for r in res["list"])
        except Exception as exc:  # noqa: BLE001
            self.store.record_error(f"funding:{symbol}", str(exc))

    async def poll_loop(self) -> None:
        """Slow REST data: OI history, long/short ratio, funding history, testnet tickers."""
        tick = 0
        while not self._stop.is_set():
            try:
                res = await self._get(TESTNET_REST, "/v5/market/tickers", {"category": "linear"})
                by_sym = {r["symbol"]: r for r in res["list"]}
                for s in self.store.symbols:
                    if s in by_sym:
                        self.store[s].testnet_ticker = {k: by_sym[s].get(k) for k in ("lastPrice", "markPrice", "bid1Price", "ask1Price")}
            except Exception as exc:  # noqa: BLE001
                self.store.record_error("testnet_tickers", str(exc))
            try:  # whole mainnet USDT-perp universe for the market map
                res = await self._get(MAINNET_REST, "/v5/market/tickers", {"category": "linear"})
                uni = []
                for r in res["list"]:
                    if not r["symbol"].endswith("USDT"):
                        continue
                    try:
                        uni.append({"symbol": r["symbol"], "last": r["lastPrice"], "pct24h": 100 * float(r["price24hPcnt"]),
                                    "turnover24h": float(r["turnover24h"]), "oi_value": float(r.get("openInterestValue") or 0),
                                    "funding": float(r.get("fundingRate") or 0)})
                    except (TypeError, ValueError):
                        continue
                uni.sort(key=lambda x: x["turnover24h"], reverse=True)
                self.store.context["universe"] = uni[:40]  # market map
                self.store.context["universe_all"] = uni[:UNIVERSE_SIZE]  # symbol picker
                self.store.context["universe_ts"] = time.time()
            except Exception as exc:  # noqa: BLE001
                self.store.record_error("universe", str(exc))
            if tick % 6 == 0:  # every ~60s
                for s in self.store.symbols:
                    await self._poll_derivs(s)
            tick += 1
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ websocket
    async def ws_loop(self) -> None:
        """One connection per WS_SHARD symbols (9 topics each), so a large universe never piles
        hundreds of topics onto one socket and a single drop only gaps a few symbols."""
        syms = list(self.store.symbols)
        shards = [syms[i:i + WS_SHARD] for i in range(0, len(syms), WS_SHARD)]
        self._shard_up = [False] * len(shards)
        await asyncio.gather(*(self._ws_shard(i, sh) for i, sh in enumerate(shards)))

    async def _ws_shard(self, idx: int, symbols: list[str]) -> None:
        topics: list[str] = []
        for s in symbols:
            topics += [f"tickers.{s}", f"kline.1.{s}", f"kline.5.{s}", f"kline.15.{s}", f"kline.60.{s}", f"kline.240.{s}",
                       f"publicTrade.{s}", f"orderbook.50.{s}", f"allLiquidation.{s}"]
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with self.session.ws_connect(MAINNET_WS, heartbeat=None, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                    for i in range(0, len(topics), 10):
                        await ws.send_json({"op": "subscribe", "args": topics[i:i + 10]})
                    self._shard_up[idx] = True
                    self.store.ws_connected = all(self._shard_up)
                    now_ms = int(time.time() * 1000)
                    for st in (self.store[s] for s in symbols):
                        # a reconnect leaves a hole in trades/liquidations: restart coverage
                        st.stream_since_ms = now_ms
                        st.flow.clear()
                    backoff = 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                await self._handle(json.loads(msg.data))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        pinger.cancel()
            except Exception as exc:  # noqa: BLE001
                self.store.record_error(f"ws:{idx}", repr(exc))
            self._shard_up[idx] = False
            self.store.ws_connected = False
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
            # resync candles after a gap so indicators do not run on holes
            await asyncio.gather(*(self._warm_symbol(s) for s in symbols))

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(20)
            await ws.send_json({"op": "ping"})

    async def _handle(self, msg: dict) -> None:
        topic = msg.get("topic")
        if not topic:
            return
        self.store.ws_messages += 1
        self.store.ws_last_msg = time.time()
        kind, _, rest = topic.partition(".")
        data = msg.get("data")
        if kind == "kline":
            tf, _, symbol = rest.partition(".")
            st = self.store.state.get(symbol)
            if not st:
                return
            for k in data:
                bar = Bar(int(k["start"]), float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"]),
                          float(k["volume"]), float(k["turnover"]), closed=bool(k["confirm"]))
                if st.upsert_bar(tf, bar):
                    for cb in self.on_bar_close:
                        asyncio.create_task(cb(symbol, tf))
            st.last_update = time.time()
        elif kind == "tickers":
            st = self.store.state.get(rest)
            if st:
                st.ticker.update({k: v for k, v in data.items() if v not in (None, "")})
                st.last_update = time.time()
        elif kind == "publicTrade":
            st = self.store.state.get(rest)
            if st:
                thr = LARGE_TRADE_USD.get(rest, DEFAULT_LARGE_TRADE_USD)
                for t in data:
                    st.add_trade(int(t["T"]), t["S"], float(t["v"]), float(t["p"]), thr)
        elif kind == "orderbook":
            _, _, symbol = rest.partition(".")
            st = self.store.state.get(symbol)
            if st:
                self._apply_book(st, msg.get("type"), data)
        elif kind == "allLiquidation":
            st = self.store.state.get(rest)
            if st:
                for liq in data:
                    st.liquidations.append((int(liq["T"]), liq["S"], float(liq["v"]) * float(liq["p"])))

    @staticmethod
    def _apply_book(st, typ: str | None, data: dict) -> None:
        if typ == "snapshot":
            st._bid_map = {float(p): float(q) for p, q in data.get("b", [])}
            st._ask_map = {float(p): float(q) for p, q in data.get("a", [])}
        else:
            bm, am = getattr(st, "_bid_map", {}), getattr(st, "_ask_map", {})
            for p, q in data.get("b", []):
                if float(q) == 0:
                    bm.pop(float(p), None)
                else:
                    bm[float(p)] = float(q)
            for p, q in data.get("a", []):
                if float(q) == 0:
                    am.pop(float(p), None)
                else:
                    am[float(p)] = float(q)
        st.bids = sorted(getattr(st, "_bid_map", {}).items(), reverse=True)[:50]
        st.asks = sorted(getattr(st, "_ask_map", {}).items())[:50]
        st.book_ts = time.time()
        if st.bids and st.asks:
            st.queue.update(st.bids[0], st.asks[0], st.book_ts)

    def stop(self) -> None:
        self._stop.set()
