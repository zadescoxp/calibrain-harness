"""Cost realism for venues whose fills are simulated (Bybit Demo Trading).

Bybit does not document how demo orders are matched or whether demo fees are charged. A demo fill
that is better than the real market would have given, or a fee that is missing, would make the
strategy look better than it is. So every demo trade is booked at the WORSE of two numbers:

- slippage: Bybit's demo fill vs walking the real mainnet order book for the same quantity at the
  moment we sent the order (entries and every bot-driven exit). Exchange-triggered stops, where we
  hold no book snapshot, are charged at least the configured stop slippage (CTB_STOP_SLIP_BP).
- fees: what Bybit charged vs the fee schedule (CTB_TAKER_FEE_BP / CTB_MAKER_FEE_BP on each leg's
  notional, maker for post-only entries and resting take-profits, taker for everything else).
- funding: Bybit's closed-P&L never includes funding, so every mainnet funding settlement the
  position was held through is charged (or credited) from the mainnet funding history.

If Bybit already charges the real costs, the adjustment is ~0 and the trade is booked exactly as
Bybit reports it. Raw venue numbers are kept next to the booked ones (venue_pnl, venue_fees,
venue_entry_slip_bp, venue_exit_slip_bp) and the breakdown is stored in realism_json.

Resting orders (post-only entries, take-profit limits) have no slippage to walk, but a simulator can
fill them on a mere touch where a real order would still be waiting in the queue. Each such fill is
checked against mainnet public trades: "through" means the market traded past our price (a real
fill was very likely), "touch" means it only traded at our price, "none" means it never got there.
These are reported, not repriced.
"""

from __future__ import annotations

from typing import Any

TOLERANCE_BP = 0.05  # differences below this are rounding, not cost


def walk_book(levels: list[tuple[float, float]], qty: float) -> dict[str, Any] | None:
    """VWAP of taking `qty` from `levels` (best first). If the book is shallower than qty, the rest is
    assumed to fill at the last visible level, which understates the cost; `short` records that."""
    if not levels or qty <= 0:
        return None
    left, cost = qty, 0.0
    for px, sz in levels:
        take = min(left, sz)
        cost += take * px
        left -= take
        if left <= 1e-12:
            break
    short = max(0.0, left)
    if short > 0:
        cost += short * levels[-1][0]
    return {"vwap": cost / qty, "best": levels[0][0], "short_qty": round(short, 12), "levels": len(levels)}


def adverse_bp(side: str, price: float, reference: float, *, entry: bool) -> float:
    if not reference:
        return 0.0
    move = (price - reference) / reference * 10000
    long_side = side == "long"
    worse_up = long_side if entry else not long_side
    return move if worse_up else -move


def extra_slip(side: str, qty: float, reference: float, venue_px: float, model_px: float | None, *, entry: bool,
               floor_bp: float = 0.0) -> dict[str, Any]:
    """Cost of the gap between the venue fill and the modelled fill, never negative."""
    venue_slip = adverse_bp(side, venue_px, reference, entry=entry)
    model_slip = adverse_bp(side, model_px, reference, entry=entry) if model_px else None
    target = max(model_slip if model_slip is not None else -1e9, floor_bp)
    eff = max(venue_slip, target)
    extra_bp = eff - venue_slip
    usd = qty * reference * extra_bp / 10000 if extra_bp > TOLERANCE_BP else 0.0
    return {"venue_slip_bp": round(venue_slip, 2), "model_slip_bp": None if model_slip is None else round(model_slip, 2),
            "floor_bp": floor_bp or None, "slip_bp": round(eff if usd else venue_slip, 2), "extra_usd": round(usd, 6)}


def expected_fees(entry_notional: float, exit_notional: float, *, maker_entry: bool, maker_exit: bool,
                  maker_bp: float, taker_bp: float) -> float:
    return (entry_notional * (maker_bp if maker_entry else taker_bp) + exit_notional * (maker_bp if maker_exit else taker_bp)) / 10000


def funding_cost(side: str, qty: float, rows: list[dict[str, Any]], opened_ts: float, closed_ts: float,
                 mark_px: float) -> dict[str, Any]:
    """Funding paid (+) or received (-) over the hold. A long pays a positive rate, a short receives it.
    `rows` are Bybit /v5/market/funding/history rows; notional is valued at mark_px (close enough for
    a position held a few hours)."""
    sign = 1 if side == "long" else -1
    paid, n = 0.0, 0
    for r in rows:
        ts = int(r.get("fundingRateTimestamp") or 0) / 1000
        if opened_ts < ts <= closed_ts:
            paid += sign * qty * mark_px * float(r.get("fundingRate") or 0)
            n += 1
    return {"usd": round(paid, 6), "settlements": n}


def queue_check(side_buys: bool, price: float, trades: list[dict[str, Any]], t0: float, t1: float) -> str:
    """Did mainnet trade THROUGH a resting order's price between t0 and t1 (seconds)?
    A resting buy is filled for real once the market sells below it; at exactly its price it may
    still be queued behind earlier orders."""
    through = touch = False
    for tr in trades:
        ts = int(tr.get("time") or 0) / 1000
        if not (t0 - 1 <= ts <= t1 + 1):
            continue
        p = float(tr.get("price") or 0)
        if (p < price) if side_buys else (p > price):
            through = True
            break
        if p == price:
            touch = True
    return "through" if through else "touch" if touch else "none"


def book_close(t: dict[str, Any], agg: dict[str, Any], venue_fees: float, reason: str, s: Any,
               funding_rows: list[dict[str, Any]] | None, venue_exit_slip_bp: float | None) -> dict[str, Any]:
    """Booked P&L of a closed demo trade = Bybit's closed P&L minus every cost Bybit under-charged.
    Returns the column updates for the trade row."""
    side, qty = t["side"], agg["size"]
    entry_px = t.get("entry_price") or 0.0
    exit_px = agg["exit_price"]
    adj: dict[str, Any] = {}
    total = 0.0

    # entry slippage (taker entries only; computed at fill time and stored on the trade)
    e = (t.get("realism_json") or {}).get("entry") or {}
    if e.get("extra_usd"):
        total += e["extra_usd"]
    adj["entry"] = e or None

    # exit slippage: bot-driven exits walked the mainnet book when they were sent; exchange stops get the floor
    exit_ref = t.get("exit_ref_px")
    if exit_ref and t.get("exit_model_px"):
        x = extra_slip(side, qty, exit_ref, exit_px, t["exit_model_px"], entry=False)
    elif reason.startswith("stop") and t.get("sl_price"):
        x = extra_slip(side, qty, t["sl_price"], exit_px, None, entry=False, floor_bp=s.stop_slip_bp)
    else:
        x = None
    if x:
        total += x["extra_usd"]
    adj["exit"] = x

    # fees
    maker_exit = reason == "take_profit" and (t.get("exit_mode") == "managed")
    exp_fees = expected_fees(qty * entry_px, qty * exit_px, maker_entry=(t.get("entry_mode") == "maker"),
                             maker_exit=maker_exit, maker_bp=s.maker_fee_bp, taker_bp=s.taker_fee_bp)
    fee_gap = max(0.0, exp_fees - venue_fees)
    if fee_gap * 10000 / max(qty * entry_px, 1e-9) <= TOLERANCE_BP:
        fee_gap = 0.0
    total += fee_gap
    adj["fees"] = {"venue_usd": round(venue_fees, 6), "schedule_usd": round(exp_fees, 6), "extra_usd": round(fee_gap, 6),
                   "maker_entry": t.get("entry_mode") == "maker", "maker_exit": maker_exit}

    # funding
    f = funding_cost(side, qty, funding_rows or [], t["opened_ts"], agg["closed_ts"], exit_px) if funding_rows is not None else None
    if f:
        total += f["usd"]
    adj["funding"] = f
    adj["total_usd"] = round(total, 6)

    upd: dict[str, Any] = {
        "venue_pnl": agg["pnl"], "venue_fees": venue_fees, "venue_exit_slip_bp": venue_exit_slip_bp,
        "realized_pnl": agg["pnl"] - total, "fees": venue_fees + fee_gap, "realism_adj_usd": round(total, 6),
        "funding_usd": f["usd"] if f else None, "realism_json": {**(t.get("realism_json") or {}), **adj},
    }
    if x and x["extra_usd"] and venue_exit_slip_bp is not None:
        # exit_slip_bp is measured against the trigger; add the slippage the venue did not charge
        upd["exit_slip_bp"] = round(venue_exit_slip_bp + x["slip_bp"] - x["venue_slip_bp"], 2)
    return upd


def queue_check_bars(side_buys: bool, price: float, bars: list[list[Any]]) -> str:
    """Same check from 1-minute mainnet klines ([start, open, high, low, close, ...]) for windows too old
    for the recent-trades endpoint. Coarser: the bar that holds the fill may trade through after it."""
    through = touch = False
    for b in bars:
        hi, lo = float(b[2]), float(b[3])
        if (lo < price) if side_buys else (hi > price):
            through = True
            break
        if lo <= price <= hi:
            touch = True
    return "through" if through else "touch" if touch else "none"


async def resting_fill_check(client: Any, symbol: str, side_buys: bool, price: float, t0: float, t1: float) -> str:
    """'through' | 'touch' | 'none' | 'unverified' for a resting order that the venue says filled."""
    try:
        trades = await client.recent_trades(symbol)
        oldest = min((int(t.get("time") or 0) / 1000 for t in trades), default=t1)
        if trades and oldest <= t0:
            return queue_check(side_buys, price, trades, t0, t1)
        bars = await client.klines_1m(symbol, int(t0 * 1000) - 60_000, int(t1 * 1000) + 60_000)
        return queue_check_bars(side_buys, price, bars) if bars else "unverified"
    except Exception:  # noqa: BLE001  (a reporting check must never break booking)
        return "unverified"
