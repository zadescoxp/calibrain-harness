"""Which perps the bot may trade: candidates, checks and validation for the symbol picker.

A symbol is only accepted if it is a Trading USDT linear perp on BOTH Bybit mainnet (signals) and
the order venue (Bybit testnet, or Demo Trading, which lists what mainnet lists). Venue depth is reported so a thin book is visible before it is chosen:
entries are capped at max_entry_slip_bp past the best price, so a book with little depth inside
that cap means small or unfilled orders.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

MAINNET = "https://api.bybit.com"
TESTNET = "https://api-testnet.bybit.com"
MAX_SYMBOLS = 30
THIN_DEPTH_USD = 5_000  # default: a book that cannot absorb one max-size order within 20 bp is "thin"

_listed_cache: dict[str, tuple[float, dict[str, str]]] = {}
_depth_cache: dict[str, tuple[float, dict[str, Any]]] = {}


async def _get(session: aiohttp.ClientSession, base: str, path: str, params: dict) -> dict:
    async with session.get(base + path, params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
        data = await r.json(content_type=None)
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"{path}: {data.get('retMsg')}")
    return data["result"]


async def listed(session: aiohttp.ClientSession, base: str) -> dict[str, str]:
    """Trading USDT linear perpetuals on a venue -> Bybit symbolType ('' = crypto, 'stock', ...). Cached 1 h."""
    hit = _listed_cache.get(base)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    out: dict[str, str] = {}
    cursor = None
    for _ in range(10):
        res = await _get(session, base, "/v5/market/instruments-info", {"category": "linear", "limit": 1000, **({"cursor": cursor} if cursor else {})})
        for it in res.get("list", []):
            if it.get("status") == "Trading" and it.get("quoteCoin") == "USDT" and it.get("contractType") == "LinearPerpetual":
                out[it["symbol"]] = it.get("symbolType") or ""
        cursor = res.get("nextPageCursor")
        if not cursor:
            break
    _listed_cache[base] = (time.time(), out)
    return out


async def testnet_depth(session: aiohttp.ClientSession, symbol: str, thin_usd: float = THIN_DEPTH_USD,
                        venue_rest: str = TESTNET) -> dict[str, Any]:
    """Resting notional within 20 bp of mid on each side of the order venue's book (cached 5 min)."""
    hit = _depth_cache.get(f"{venue_rest}|{symbol}")
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        res = await _get(session, venue_rest, "/v5/market/orderbook", {"category": "linear", "symbol": symbol, "limit": 200})
        bids = [(float(p), float(q)) for p, q in res.get("b", [])]
        asks = [(float(p), float(q)) for p, q in res.get("a", [])]
        if not bids or not asks:
            out: dict[str, Any] = {"ok": False, "reason": "empty book"}
        else:
            mid = (bids[0][0] + asks[0][0]) / 2
            lim = mid * 0.002
            b = sum(p * q for p, q in bids if mid - p <= lim)
            a = sum(p * q for p, q in asks if p - mid <= lim)
            out = {"ok": True, "bid_usd_20bp": round(b), "ask_usd_20bp": round(a), "spread_bp": round((asks[0][0] - bids[0][0]) / mid * 1e4, 2),
                   "thin": min(a, b) < thin_usd}
    except Exception as exc:  # noqa: BLE001
        out = {"ok": False, "reason": str(exc)[:120]}
    _depth_cache[f"{venue_rest}|{symbol}"] = (time.time(), out)
    return out


async def candidates(session: aiohttp.ClientSession, universe: list[dict[str, Any]], selected: list[str],
                     thin_usd: float = THIN_DEPTH_USD, venue_rest: str = TESTNET) -> list[dict[str, Any]]:
    main, test = await asyncio.gather(listed(session, MAINNET), listed(session, venue_rest))
    by_sym = {u["symbol"]: u for u in universe}
    syms = list(dict.fromkeys([*selected, *by_sym]))
    sem = asyncio.Semaphore(8)

    async def depth(sym: str) -> dict[str, Any] | None:
        if sym not in test:
            return None
        async with sem:
            return await testnet_depth(session, sym, thin_usd, venue_rest)

    depths = await asyncio.gather(*(depth(s) for s in syms))
    out = []
    for sym, dp in zip(syms, depths):
        u = by_sym.get(sym, {})
        out.append({"symbol": sym, "selected": sym in selected, "turnover24h": u.get("turnover24h"), "pct24h": u.get("pct24h"),
                    "last": u.get("last"), "mainnet": sym in main, "testnet": sym in test, "testnet_book": dp,
                    # stock/commodity perps track markets that close: weekends and nights behave unlike crypto
                    "asset_type": main.get(sym) or "crypto"})
    return out


async def validate(session: aiohttp.ClientSession, symbols: list[str], venue_rest: str = TESTNET,
                   venue_name: str = "testnet") -> list[str]:
    errs: list[str] = []
    if not symbols:
        errs.append("choose at least one symbol")
    if len(symbols) > MAX_SYMBOLS:
        errs.append(f"at most {MAX_SYMBOLS} symbols")
    if len(set(symbols)) != len(symbols):
        errs.append("duplicate symbols")
    bad = [s for s in symbols if not (s.isalnum() and s.endswith("USDT"))]
    if bad:
        errs.append(f"not USDT perp symbols: {', '.join(bad)}")
    if errs:
        return errs
    main, test = await asyncio.gather(listed(session, MAINNET), listed(session, venue_rest))
    if missing := [s for s in symbols if s not in main]:
        errs.append(f"not a trading perp on Bybit mainnet (no signals): {', '.join(missing)}")
    if missing := [s for s in symbols if s not in test]:
        errs.append(f"not listed on Bybit {venue_name} (cannot trade): {', '.join(missing)}")
    return errs
