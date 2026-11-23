"""Where orders go. Two venues, both on demo funds; there is deliberately no live-funds venue.

- testnet: api-testnet.bybit.com. Its own (thin, often dislocated) order book and its own prices.
- demo:    api-demo.bybit.com. Bybit's Demo Trading account on MAINNET market data: orders are
           matched by Bybit against the real mainnet market with demo funds. Bybit serves no public
           market data on the demo domain, so instruments, tickers, order books and the ticker stream
           come from the mainnet public API (no keys sent there). Demo keys are created on bybit.com
           after switching to "Demo Trading"; testnet keys do not work here and vice versa.

Pointing the bot at a real-money account would need a new venue entry here and a code review,
not an env flag: PRIVATE_HOSTS is the allowlist the signed client refuses to step outside.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    name: str             # testnet | demo
    label: str            # shown in the UI
    private_rest: str     # signed account/trading endpoints
    public_rest: str      # instruments, tickers, order book, server time
    private_ws: str
    public_ws: str        # ticker stream of the book we trade on
    keys_url: str
    # Demo fills and fees are Bybit's simulation and their mechanics are not documented, so every
    # fill is checked against the real mainnet book and the fee schedule, and the worse of the two
    # is what the trade is booked at (ctb/broker/realism.py). Testnet fills are real fills on the
    # book we trade on, so they are booked as reported.
    shadow_costs: bool


VENUES: dict[str, Venue] = {
    "testnet": Venue(
        name="testnet", label="BYBIT TESTNET",
        private_rest="https://api-testnet.bybit.com", public_rest="https://api-testnet.bybit.com",
        private_ws="wss://stream-testnet.bybit.com/v5/private", public_ws="wss://stream-testnet.bybit.com/v5/public/linear",
        keys_url="https://testnet.bybit.com/app/user/api-management", shadow_costs=False,
    ),
    "demo": Venue(
        name="demo", label="BYBIT DEMO",
        private_rest="https://api-demo.bybit.com", public_rest="https://api.bybit.com",
        private_ws="wss://stream-demo.bybit.com/v5/private", public_ws="wss://stream.bybit.com/v5/public/linear",
        keys_url="https://www.bybit.com/app/user/api-management", shadow_costs=True,
    ),
}

PRIVATE_HOSTS = frozenset(v.private_rest for v in VENUES.values())


def get(name: str) -> Venue:
    try:
        return VENUES[(name or "testnet").strip().lower()]
    except KeyError:
        raise ValueError(f"CTB_VENUE must be one of {', '.join(VENUES)}") from None
