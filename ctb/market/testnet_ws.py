"""Bybit TESTNET public ticker stream (bid/ask/last of the book we actually trade on).

Signals come from mainnet, but every order fills on the testnet book, which can sit 100+ bp away
from mainnet and is far thinner. Managed stops are triggered from this stream's bid (long) or ask
(short), i.e. the price we could actually exit at, within milliseconds of it moving.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import aiohttp

from .store import MarketStore

TESTNET_PUBLIC_WS = "wss://stream-testnet.bybit.com/v5/public/linear"
log = logging.getLogger("ctb.testnet_ws")

OnTick = Callable[[str, dict], Awaitable[None]]


class TestnetTickerStream:
    def __init__(self, store: MarketStore, session: aiohttp.ClientSession, symbols: list[str]):
        self.store = store
        self.session = session
        self.symbols = symbols
        self.on_tick: list[OnTick] = []
        self.connected = False
        self.last_msg_ts = 0.0
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                async with self.session.ws_connect(TESTNET_PUBLIC_WS, heartbeat=None) as ws:
                    args = [f"tickers.{s}" for s in self.symbols]
                    for i in range(0, len(args), 10):  # Bybit accepts at most 10 args per subscribe
                        await ws.send_json({"op": "subscribe", "args": args[i:i + 10]})
                    self.connected, backoff = True, 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            data = json.loads(msg.data)
                            topic = data.get("topic", "")
                            if not topic.startswith("tickers."):
                                continue
                            sym = topic.split(".", 1)[1]
                            d = data.get("data") or {}
                            t = self.store[sym].testnet_ticker
                            for k in ("lastPrice", "markPrice", "bid1Price", "ask1Price", "bid1Size", "ask1Size"):
                                if d.get(k):
                                    t[k] = d[k]
                            t["ts"] = time.time()
                            self.last_msg_ts = t["ts"]
                            for cb in self.on_tick:
                                try:
                                    await cb(sym, t)
                                except Exception as exc:  # noqa: BLE001
                                    log.exception("testnet tick handler failed: %s", exc)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("testnet ticker ws: %s", exc)
            self.connected = False
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    async def _ping(ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(20)
            await ws.send_json({"op": "ping"})
