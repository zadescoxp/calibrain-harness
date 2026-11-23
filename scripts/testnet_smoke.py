"""Execution round trip at minimum size on the configured venue (Bybit testnet or Demo Trading): open with server-side TP/SL, verify the
stop is on the position, then close reduce-only. Proves the execution path before the bot uses it.

    .venv/bin/python scripts/testnet_smoke.py SOLUSDT long          # entry mode from settings (maker by default)
    .venv/bin/python scripts/testnet_smoke.py SOLUSDT long taker    # capped IOC entry: fills at once
"""
import asyncio
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp  # noqa: E402

from ctb.broker.executor import Executor  # noqa: E402
from ctb.config import load_settings  # noqa: E402
from ctb.db import DB  # noqa: E402
from ctb.broker.bybit_rest import BybitTestnet  # noqa: E402


async def main(symbol: str, side: str, entry_mode: str | None = None) -> None:
    s = load_settings()
    if entry_mode:
        s.entry_mode = entry_mode
    s.symbols = [symbol]
    s.trading_enabled = True
    s.risk_per_trade_pct = 0.0005  # tiny: sizing floors to the exchange minimum or near it
    s.db_path = s.db_path.with_name("smoke.db")
    async with aiohttp.ClientSession() as session:
        ex = Executor(s, DB(s.db_path), BybitTestnet(s.bybit_api_key, s.bybit_api_secret, session, s.venue), lambda k, d: print(f"[{k}] {d.get('message', d)}"))
        await ex.start()
        print(f"entry: {s.entry_mode}" + (f" (post-only, waits {s.maker_wait_s:g}s; add 'taker' to fill at once)" if s.entry_mode == "maker" else ""))
        print(f"venue: {s.venue_spec.label} ({ex.client.host}){' + cost realism' if s.shadow_costs else ''} · equity {ex.wallet.get('equity')}")
        inst = ex.instruments[symbol]
        tk = await ex.client.ticker(symbol)
        min_risk_pct = 100 * inst["min_qty"] * float(tk["lastPrice"]) * 0.006 / ex.wallet["equity"]
        s.risk_per_trade_pct = max(s.risk_per_trade_pct, min_risk_pct * 1.5)
        res = await ex.open(symbol=symbol, side=side, sl_bp=60, tp_bp=100, decision_id=0, ev_r=0.0)
        print("open:", res)
        if not res.get("ok"):
            return
        pos = ex.position(symbol)
        print(f"position: side={pos['side']} size={pos['size']} entry={pos['avgPrice']} TP={pos['takeProfit']} SL={pos['stopLoss']} "
              f"tpslMode={pos.get('tpslMode')} lev={pos['leverage']}")
        assert float(pos["stopLoss"]) > 0, "stop loss missing on position"
        await asyncio.sleep(2)
        print("close:", await ex.close(symbol, "smoke_test"))
        # Bybit posts closed-P&L rows a moment after the fill; retry, then book whatever is there
        for attempt in range(10):
            await asyncio.sleep(2)
            await ex.sync()
            rows = ex.db.query("SELECT * FROM trades WHERE status='open'")
            if not rows:
                break
            for row in rows:
                ex._flat_since.setdefault(row["id"], time.time())
                await ex.record_close(row, force=attempt == 9)
        for row in ex.db.query("SELECT venue, symbol, side, qty, entry_mode, entry_price, exit_price, realized_pnl, venue_pnl, fees, "
                               "venue_fees, realism_adj_usd, funding_usd, entry_slip_bp, venue_entry_slip_bp, exit_slip_bp, "
                               "maker_fill_check, close_reason, protection, status FROM trades ORDER BY id DESC LIMIT 1"):
            print("recorded:", {k: v for k, v in row.items() if v is not None})
        print("remaining positions:", [p["symbol"] for p in ex.positions])


mode = sys.argv[3].lower() if len(sys.argv) > 3 else None
if mode not in (None, "maker", "taker"):
    sys.exit("entry mode must be maker or taker")
asyncio.run(main(sys.argv[1].upper() if len(sys.argv) > 1 else "SOLUSDT", sys.argv[2] if len(sys.argv) > 2 else "long", mode))
