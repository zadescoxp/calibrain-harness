"""Execution cost model: what each bracket outcome really costs, from fees and measured slippage.

One flat round-trip cost hides the fact that outcomes are executed differently:
- entry: a post-only maker limit (maker fee, no slippage) or a capped IOC taker order (taker fee + slip);
- target: a resting reduce-only limit, so always the maker fee and no slippage;
- stop: triggered, then exited with stepped IOC orders (taker fee + stop slippage, the expensive one);
- time exit: an IOC close at the horizon (taker fee + a small slip).

Slippage defaults are replaced by the medians of the bot's own recent closed trades as soon as there
are enough of them, so the cost the policy charges is the cost the harness is actually paying.
All numbers are in bp of notional.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

from .config import Settings
from .db import DB

MIN_MEASURED = 8
REFRESH_S = 300


class CostModel:
    def __init__(self, s: Settings, db: DB | None = None):
        self.s = s
        self.db = db
        self.measured: dict[str, Any] = {}
        self.refreshed_at = 0.0

    def refresh(self, force: bool = False) -> dict[str, Any]:
        if self.db is None or (not force and time.time() - self.refreshed_at < REFRESH_S):
            return self.measured
        rows = self.db.query("SELECT entry_slip_bp, exit_slip_bp, close_reason, entry_mode FROM trades "
                             "WHERE status='closed' ORDER BY closed_ts DESC LIMIT 200")
        def med(xs: list[float]) -> float | None:
            xs = [x for x in xs if x is not None]
            return statistics.median(xs) if len(xs) >= MIN_MEASURED else None
        taker_entries = [r["entry_slip_bp"] for r in rows if (r.get("entry_mode") or "taker") == "taker"]
        maker = self.db.query("SELECT status FROM trades WHERE entry_mode='maker' ORDER BY id DESC LIMIT 200")
        filled = sum(1 for r in maker if r["status"] not in ("unfilled", "error"))
        self.measured = {
            "entry_slip_bp": med(taker_entries),
            "stop_slip_bp": med([r["exit_slip_bp"] for r in rows if (r["close_reason"] or "").startswith("stop")]),
            "maker_fill_rate": round(filled / len(maker), 3) if len(maker) >= MIN_MEASURED else None,
            "trades_used": len(rows),
        }
        self.refreshed_at = time.time()
        return self.measured

    def outcome_costs(self, entry_mode: str | None = None) -> dict[str, Any]:
        s = self.s
        m = self.refresh()
        mode = entry_mode or s.entry_mode
        entry_slip = m.get("entry_slip_bp")
        stop_slip = m.get("stop_slip_bp")
        if mode == "maker":
            entry = s.maker_fee_bp
        else:
            entry = s.taker_fee_bp + max(0.0, entry_slip if entry_slip is not None else s.slippage_bp)
        stop_extra = max(0.0, stop_slip if stop_slip is not None else s.stop_slip_bp)
        out = {
            "entry_mode": mode,
            "entry_bp": round(entry, 2),
            "target_bp": round(entry + s.maker_fee_bp, 2),
            "stop_bp": round(entry + s.taker_fee_bp + stop_extra, 2),
            "time_bp": round(entry + s.taker_fee_bp + s.time_exit_slip_bp, 2),
            "source": {"entry_slip": "measured" if (mode != "maker" and entry_slip is not None) else "default",
                       "stop_slip": "measured" if stop_slip is not None else "default"},
            "maker_fill_rate": m.get("maker_fill_rate"),
        }
        out["reference_bp"] = round((out["target_bp"] + out["stop_bp"]) / 2, 2)
        return out


def bracket_ev(p_target: float, p_stop: float, rr: float, sl_bp: float, costs: dict[str, Any] | float) -> float:
    """Expected value in R of a bracket, charging each outcome its own cost.

        EV = P(t) * (RR - c_t/SL) - P(s) * (1 + c_s/SL) - P(n) * c_n/SL

    P(neither) is closed at the horizon near entry on average (no drift assumed), so it pays only its
    exit cost. A plain number for `costs` charges that cost to every outcome (the old flat model)."""
    if isinstance(costs, (int, float)):
        ct = cs = cn = float(costs)
    else:
        ct, cs, cn = costs["target_bp"], costs["stop_bp"], costs["time_bp"]
    p_n = max(0.0, 1 - p_target - p_stop)
    return p_target * (rr - ct / sl_bp) - p_stop * (1 + cs / sl_bp) - p_n * (cn / sl_bp)


def outcome_cost_bp(outcome: str, costs: dict[str, Any] | float) -> float:
    if isinstance(costs, (int, float)):
        return float(costs)
    return {"target_first": costs["target_bp"], "stop_first": costs["stop_bp"]}.get(outcome, costs["time_bp"])
