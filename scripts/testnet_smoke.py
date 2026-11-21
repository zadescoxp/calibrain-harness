"""Bybit TESTNET execution round trip at minimum size: open with server-side TP/SL, verify the
stop is on the position, then close reduce-only. Proves the execution path before the bot uses it.

    .venv/bin/python scripts/testnet_smoke.py SOLUSDT long
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp  # noqa: E402

from ctb.broker.executor import Executor  # noqa: E402
from ctb.config import load_settings  # noqa: E402
from ctb.db import DB  # noqa: E402
from ctb.broker.bybit_rest import BybitTestnet  # noqa: E402


async def main(symbol: str, side: str) -> None:
    s = load_settings()
    s.symbols = [symbol]
    s.trading_enabled = True
    s.risk_per_trade_pct = 0.0005  # tiny: sizing floors to the exchange minimum or near it
    s.db_path = s.db_path.with_name("smoke.db")
    async with aiohttp.ClientSession() as session:
        ex = Executor(s, DB(s.db_path), BybitTestnet(s.bybit_api_key, s.bybit_api_secret, session), lambda k, d: print(f"[{k}] {d.get('message', d)}"))
        await ex.start()
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
        await asyncio.sleep(3)
        for _ in range(10):  # order history can lag a few seconds (Demo Trading: ~4 s)
            rows = ex.db.query("SELECT * FROM trades WHERE status='open'")
            if not rows:
                break
            for row in rows:
                await ex.record_close(row)
            await asyncio.sleep(2)
        for row in ex.db.query("SELECT symbol, side, qty, entry_price, exit_price, realized_pnl, fees, close_reason, protection FROM trades ORDER BY id DESC LIMIT 1"):
            print("recorded:", row)
        print("remaining positions:", [p["symbol"] for p in ex.positions])


asyncio.run(main(sys.argv[1].upper() if len(sys.argv) > 1 else "SOLUSDT", sys.argv[2] if len(sys.argv) > 2 else "long"))
