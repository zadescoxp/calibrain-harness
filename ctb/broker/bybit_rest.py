"""Minimal signed Bybit v5 REST client for USDT linear perpetuals. TESTNET ONLY.

The host is hard-coded to api-testnet.bybit.com. There is deliberately no switch to mainnet:
pointing this bot at real money would need a code change and a review, not an env flag.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import aiohttp

TESTNET_HOST = "https://api-testnet.bybit.com"
RECV_WINDOW = "10000"


class BybitError(Exception):
    def __init__(self, ret_code: int, ret_msg: str, path: str):
        super().__init__(f"Bybit {path} -> {ret_code}: {ret_msg}")
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.path = path


class BybitTestnet:
    def __init__(self, api_key: str, api_secret: str, session: aiohttp.ClientSession):
        if not api_key or not api_secret:
            raise ValueError("BYBIT_API_KEY / BYBIT_API_SECRET (testnet) are required")
        self._key = api_key
        self._secret = api_secret.encode()
        self._session = session
        self.host = TESTNET_HOST
        self._time_offset_ms = 0

    # ------------------------------------------------------------------ transport
    async def sync_time(self) -> None:
        async with self._session.get(f"{self.host}/v5/market/time", timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json(content_type=None)
        server_ms = int(data["result"]["timeNano"]) // 1_000_000
        self._time_offset_ms = server_ms - int(time.time() * 1000)

    def _sign(self, ts: str, payload: str) -> str:
        msg = f"{ts}{self._key}{RECV_WINDOW}{payload}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    async def _request(self, method: str, path: str, params: dict[str, Any] | None = None, *, retries: int = 2) -> dict[str, Any]:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(retries + 1):
            ts = str(int(time.time() * 1000) + self._time_offset_ms)
            if method == "GET":
                query = urllib.parse.urlencode(sorted(params.items()))
                payload, url, body = query, f"{self.host}{path}" + (f"?{query}" if query else ""), None
            else:
                body = json.dumps(params, separators=(",", ":"))
                payload, url = body, f"{self.host}{path}"
            headers = {
                "X-BAPI-API-KEY": self._key,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": RECV_WINDOW,
                "X-BAPI-SIGN": self._sign(ts, payload),
                "Content-Type": "application/json",
            }
            try:
                async with self._session.request(method, url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    status = r.status
                    try:
                        data = await r.json(content_type=None)
                    except ValueError:
                        data = None
                if not isinstance(data, dict):
                    # Bybit answers an unknown/invalid key with a bare HTTP 401 and no body.
                    msg = "invalid API key, or a mainnet key used on testnet" if status in (401, 403) else f"HTTP {status} with no JSON body"
                    raise BybitError(10003 if status in (401, 403) else -status, msg, path)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt < retries and method == "GET":
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise
            code = int(data.get("retCode", -1))
            if code == 10002 and attempt < retries:  # timestamp outside recv window
                await self.sync_time()
                continue
            if code == 10006 and attempt < retries:  # rate limited
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            if code != 0:
                raise BybitError(code, str(data.get("retMsg")), path)
            return data.get("result") or {}
        raise BybitError(-1, "retries exhausted", path)

    # ------------------------------------------------------------------ public (testnet)
    async def instruments(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for sym in symbols:
            res = await self._request("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": sym})
            for item in res.get("list", []):
                lot, price = item["lotSizeFilter"], item["priceFilter"]
                out[sym] = {
                    "qty_step": float(lot["qtyStep"]),
                    "min_qty": float(lot["minOrderQty"]),
                    "max_qty": float(lot.get("maxMktOrderQty") or lot["maxOrderQty"]),
                    "min_notional": float(lot.get("minNotionalValue") or 5),
                    "tick_size": float(price["tickSize"]),
                    "max_leverage": float(item["leverageFilter"]["maxLeverage"]),
                }
        return out

    async def ticker(self, symbol: str) -> dict[str, Any]:
        res = await self._request("GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol})
        return res["list"][0]

    async def orderbook(self, symbol: str, depth: int = 50) -> dict[str, list[tuple[float, float]]]:
        """Testnet book, best first: {"bids": [(price, size), ...], "asks": [...]}. Sizing and the
        entry price cap are computed from this, because testnet depth is far thinner than mainnet."""
        res = await self._request("GET", "/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": depth})
        return {"bids": [(float(p), float(q)) for p, q in res.get("b", [])],
                "asks": [(float(p), float(q)) for p, q in res.get("a", [])]}

    # ------------------------------------------------------------------ account
    async def wallet(self) -> dict[str, Any]:
        res = await self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        acct = (res.get("list") or [{}])[0]
        usdt = next((c for c in acct.get("coin", []) if c.get("coin") == "USDT"), {})

        def f(x: Any) -> float:
            try:
                return float(x)
            except (TypeError, ValueError):
                return 0.0

        return {
            "equity": f(acct.get("totalEquity")),
            "wallet_balance": f(acct.get("totalWalletBalance")),
            "available": f(acct.get("totalAvailableBalance")),
            "margin_balance": f(acct.get("totalMarginBalance")),
            "initial_margin": f(acct.get("totalInitialMargin")),
            "maintenance_margin": f(acct.get("totalMaintenanceMargin")),
            "unrealized_pnl": f(usdt.get("unrealisedPnl")),
            "usdt_equity": f(usdt.get("equity")),
            "usdt_wallet": f(usdt.get("walletBalance")),
            "cum_realized_pnl": f(usdt.get("cumRealisedPnl")),
        }

    async def positions(self) -> list[dict[str, Any]]:
        res = await self._request("GET", "/v5/position/list", {"category": "linear", "settleCoin": "USDT"})
        return [p for p in res.get("list", []) if float(p.get("size") or 0) > 0]

    async def open_orders(self) -> list[dict[str, Any]]:
        res = await self._request("GET", "/v5/order/realtime", {"category": "linear", "settleCoin": "USDT"})
        return res.get("list", [])

    async def closed_pnl(self, limit: int = 100, start_ms: int | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
        """One row per closing order. A single exit that sweeps several book levels can post
        several rows; callers must aggregate them (see Executor._record_close)."""
        params = {"category": "linear", "limit": limit, "startTime": start_ms, "symbol": symbol}
        res = await self._request("GET", "/v5/position/closed-pnl", params)
        return res.get("list", [])

    async def executions(self, symbol: str | None = None, limit: int = 100, start_ms: int | None = None,
                         order_id: str | None = None) -> list[dict[str, Any]]:
        params = {"category": "linear", "limit": limit, "symbol": symbol, "startTime": start_ms, "orderId": order_id}
        res = await self._request("GET", "/v5/execution/list", params)
        return res.get("list", [])

    async def order_history(self, symbol: str, order_link_id: str | None = None, order_id: str | None = None) -> dict[str, Any] | None:
        params = {"category": "linear", "symbol": symbol, "orderLinkId": order_link_id, "orderId": order_id}
        res = await self._request("GET", "/v5/order/history", params)
        lst = res.get("list", [])
        return lst[0] if lst else None

    # ------------------------------------------------------------------ trading
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            await self._request("POST", "/v5/position/set-leverage", {
                "category": "linear", "symbol": symbol,
                "buyLeverage": str(leverage), "sellLeverage": str(leverage),
            })
        except BybitError as exc:
            if exc.ret_code != 110043:  # leverage not modified
                raise

    async def market_order(
        self, symbol: str, side: str, qty: str, *, link_id: str,
        take_profit: str | None = None, stop_loss: str | None = None, reduce_only: bool = False,
        limit_price: str | None = None, trigger_by: str = "LastPrice",
    ) -> dict[str, Any]:
        """IOC order. With limit_price it is a marketable limit: it takes whatever liquidity exists
        up to that price and cancels the rest, so it can never sweep a thin book."""
        params: dict[str, Any] = {
            "category": "linear", "symbol": symbol, "side": "Buy" if side == "buy" else "Sell",
            "orderType": "Limit" if limit_price else "Market", "qty": qty, "positionIdx": 0, "orderLinkId": link_id,
            "reduceOnly": reduce_only, "timeInForce": "IOC",
        }
        if limit_price:
            params["price"] = limit_price
        if take_profit or stop_loss:
            # Position-level (Full) TP/SL. On testnet the mark price follows the mainnet index while
            # the testnet book can trade 100+ bp away from it, so a mark-triggered exit fires at a
            # level the book is nowhere near and then fills far off. LastPrice triggers where we can trade.
            params.update({"tpslMode": "Full", "tpTriggerBy": trigger_by, "slTriggerBy": trigger_by})
            if take_profit:
                params["takeProfit"] = take_profit
            if stop_loss:
                params["stopLoss"] = stop_loss
        return await self._request("POST", "/v5/order/create", params)

    async def set_trading_stop(self, symbol: str, *, take_profit: str | None, stop_loss: str | None,
                               trigger_by: str = "LastPrice") -> None:
        params = {"category": "linear", "symbol": symbol, "positionIdx": 0, "tpslMode": "Full",
                  "takeProfit": take_profit, "stopLoss": stop_loss}
        if take_profit and take_profit != "0":
            params["tpTriggerBy"] = trigger_by
        if stop_loss and stop_loss != "0":
            params["slTriggerBy"] = trigger_by
        await self._request("POST", "/v5/position/trading-stop", params)

    async def limit_order(self, symbol: str, side: str, qty: str, price: str, *, link_id: str,
                          reduce_only: bool = True, tif: str = "GTC", stop_loss: str | None = None,
                          trigger_by: str = "MarkPrice") -> dict[str, Any]:
        """Resting limit order. Take-profits: reduce-only GTC (fills at the target or better, never worse).
        Maker entries: tif="PostOnly" (rejected instead of crossing the spread) with the backstop stop attached."""
        params: dict[str, Any] = {
            "category": "linear", "symbol": symbol, "side": "Buy" if side == "buy" else "Sell", "orderType": "Limit",
            "qty": qty, "price": price, "positionIdx": 0, "orderLinkId": link_id, "reduceOnly": reduce_only, "timeInForce": tif,
        }
        if stop_loss:
            params.update({"tpslMode": "Full", "stopLoss": stop_loss, "slTriggerBy": trigger_by})
        return await self._request("POST", "/v5/order/create", params)

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            await self._request("POST", "/v5/order/cancel", {"category": "linear", "symbol": symbol, "orderId": order_id})
        except BybitError as exc:
            if exc.ret_code not in (110001, 170213):  # already filled / cancelled / not found
                raise

    async def cancel_all(self, symbol: str | None = None) -> None:
        params = {"category": "linear", "symbol": symbol} if symbol else {"category": "linear", "settleCoin": "USDT"}
        await self._request("POST", "/v5/order/cancel-all", params)
