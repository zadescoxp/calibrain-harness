"""Bybit testnet private WebSocket: position, execution, order and wallet pushes.

Polling every 5 s means a stop that fires is noticed up to 5 s late and the dashboard shows a
stale account. The private stream tells us within milliseconds, so closes are recorded as soon as
Bybit has booked them. The REST reconcile loop stays as the fallback if the stream drops.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Awaitable, Callable

import aiohttp

from .. import venue

TESTNET_PRIVATE_WS = "wss://stream-testnet.bybit.com/v5/private"
TOPICS = ["position.linear", "execution.linear", "order.linear", "wallet"]

log = logging.getLogger("ctb.private_ws")

Handler = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


class BybitPrivateStream:
    def __init__(self, api_key: str, api_secret: str, session: aiohttp.ClientSession, on_message: Handler):
        self._key = api_key
        self._secret = api_secret.encode()
        self._session = session
        self._on_message = on_message
        self._stop = False
        self.connected = False
        self.last_msg_ts = 0.0
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop = True

    def _auth_args(self) -> list[Any]:
        expires = int((time.time() + 10) * 1000)
        sig = hmac.new(self._secret, f"GET/realtime{expires}".encode(), hashlib.sha256).hexdigest()
        return [self._key, expires, sig]

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                async with self._session.ws_connect(venue.get()["private_ws"], heartbeat=None, timeout=aiohttp.ClientWSTimeout(ws_close=10)) as ws:
                    await ws.send_json({"op": "auth", "args": self._auth_args()})
                    auth = await ws.receive_json(timeout=10)
                    if not auth.get("success"):
                        raise RuntimeError(f"private ws auth failed: {auth.get('ret_msg')}")
                    await ws.send_json({"op": "subscribe", "args": TOPICS})
                    self.connected, self.last_error, backoff = True, None, 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            data = json.loads(msg.data)
                            topic = data.get("topic")
                            if topic and isinstance(data.get("data"), list):
                                self.last_msg_ts = time.time()
                                try:
                                    await self._on_message(topic.split(".")[0], data["data"])
                                except Exception as exc:  # noqa: BLE001
                                    log.exception("private ws handler failed: %s", exc)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.warning("private ws: %s", exc)
            self.connected = False
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    async def _ping(ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(20)
            await ws.send_json({"op": "ping"})
