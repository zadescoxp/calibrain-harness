"""Re-book every past trade from Bybit's own records.

Earlier versions kept only the last closed-P&L row of an exit and could leave a trade in
'opening' if its stop fired before bookkeeping finished. This script recovers those trades and
recomputes P&L, fees, exit price, exit reason and R from all closed-P&L rows, so the database
(and every report built from it) matches the Bybit trade statement exactly.

Usage: .venv/bin/python scripts/backfill_trades.py [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ctb.broker.bybit_rest import BybitTestnet  # noqa: E402
from ctb.broker.executor import Executor  # noqa: E402
from ctb.config import load_settings  # noqa: E402
from ctb.db import DB  # noqa: E402

KEEP_REASONS = {"time_stop", "kill", "unprotected", "stop_on_entry", "target_on_entry"}  # set by the bot itself


async def main(dry_run: bool) -> None:
    s = load_settings()
    db = DB(s.db_path)
    async with aiohttp.ClientSession() as session:
        client = BybitTestnet(s.bybit_api_key, s.bybit_api_secret, session, s.venue)
        await client.sync_time()
        ex = Executor(s, db, client, lambda *_: None)
        before = {t["id"]: t for t in db.query("SELECT * FROM trades")}
        for t in db.query("SELECT * FROM trades WHERE status='opening'"):
            print(f"#{t['id']} {t['symbol']} stuck in 'opening': recovering from order history")
            if not dry_run:
                await ex.resolve_opening(t)
        for t in db.query("SELECT * FROM trades WHERE status IN ('open','closed')"):
            if t["status"] == "open":
                live = await client.positions()
                if any(p["symbol"] == t["symbol"] for p in live):
                    continue  # really still open
            t = dict(t)
            if t.get("close_reason") not in KEEP_REASONS:
                t["close_reason"] = None  # re-derive from the closing orders
            if dry_run:
                continue
            ok = await ex.record_close(t, force=True)
            if not ok:
                print(f"#{t['id']} {t['symbol']}: no closed-P&L rows found")
        if dry_run:
            print("dry run: nothing written")
            return
        total_old = total_new = 0.0
        for t in db.query("SELECT * FROM trades WHERE status='closed' ORDER BY id"):
            old = before.get(t["id"], {})
            o, n = old.get("realized_pnl"), t["realized_pnl"]
            total_old += o or 0
            total_new += n or 0
            flag = "" if o is not None and abs((o or 0) - n) < 0.005 else "  <- corrected"
            print(f"#{t['id']:>3} {t['symbol']:9} {t['side']:5} pnl {('%.2f' % o) if o is not None else 'none':>9} -> {n:9.2f} "
                  f"fills {t['exit_fills'] or 1} fees {t['fees']:.2f} {t['close_reason']:<12} R {t['r_mult'] if t['r_mult'] is None else round(t['r_mult'], 2)}{flag}")
        print(f"total {total_old:.2f} -> {total_new:.2f} USDT")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
