"""Where orders go: Bybit TESTNET (default) or Bybit DEMO TRADING. Never mainnet.

CTB_VENUE=testnet | demo, switchable from the dashboard (KEYS) while the bot has no open trades.
Each venue keeps its own keys, so switching never mixes accounts:
testnet BYBIT_API_KEY / BYBIT_API_SECRET, demo BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET.

- testnet: testnet.bybit.com, its own (thin, drifting) books and prices.
- demo:    Bybit Demo Trading (keys made on bybit.com in Demo Trading mode). Simulated fills at real
           mainnet prices; Bybit serves no separate public data for it, so the venue's public prices
           and books come from mainnet.

Signed requests can only ever reach the two hosts in TRADING_HOSTS. There is deliberately no mainnet
entry: pointing this bot at real money needs a code change and a review, not an env flag.
"""

from __future__ import annotations

import os

VENUES: dict[str, dict[str, str]] = {
    "testnet": {
        "rest": "https://api-testnet.bybit.com",
        "private_ws": "wss://stream-testnet.bybit.com/v5/private",
        "public_ws": "wss://stream-testnet.bybit.com/v5/public/linear",
        "market_rest": "https://api-testnet.bybit.com",
        "label": "BYBIT TESTNET",
        "keys_url": "https://testnet.bybit.com/app/user/api-management",
        "key_env": "BYBIT_API_KEY",
        "secret_env": "BYBIT_API_SECRET",
    },
    "demo": {
        "rest": "https://api-demo.bybit.com",
        "private_ws": "wss://stream-demo.bybit.com/v5/private",
        "public_ws": "wss://stream.bybit.com/v5/public/linear",
        "market_rest": "https://api.bybit.com",  # public GETs only, never signed
        "label": "BYBIT DEMO",
        "keys_url": "https://www.bybit.com/en/app/user/api-management",
        "key_env": "BYBIT_DEMO_API_KEY",
        "secret_env": "BYBIT_DEMO_API_SECRET",
    },
}
# Demo quirks (measured 2026-11-14): /v5/execution/list, /v5/position/closed-pnl and the transaction log
# stay empty, and /v5/order/history lags ~4 s, while /v5/order/realtime (~0.5 s) and the private
# WebSocket (~0.2 s) are prompt. The executor therefore reads fills from order/realtime and, on demo,
# rebuilds closed-P&L rows from the trade's closing orders (executor.closed_rows_from_orders).
ORDERS_UNSUPPORTED: dict[str, str] = {}


def orders_blocked() -> str | None:
    return ORDERS_UNSUPPORTED.get(name())


TRADING_HOSTS = frozenset({"https://api-testnet.bybit.com", "https://api-demo.bybit.com"})


def name() -> str:
    v = os.environ.get("CTB_VENUE", "testnet").strip().lower()
    if v not in VENUES:
        raise ValueError(f"CTB_VENUE={v!r}; use testnet or demo")
    return v


def get() -> dict[str, str]:
    return VENUES[name()]


def keys(venue: str | None = None) -> tuple[str, str]:
    """(api key, api secret) for a venue, default the current one. Empty strings when not set."""
    v = VENUES[venue or name()]
    return os.environ.get(v["key_env"], "").strip(), os.environ.get(v["secret_env"], "").strip()
