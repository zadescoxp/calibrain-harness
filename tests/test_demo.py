"""Bybit Demo Trading venue and the cost-realism layer."""

from __future__ import annotations

import asyncio
import time

import pytest

from ctb.broker import realism, venue
from ctb.config import Settings
from ctb.db import DB


def demo() -> Settings:
    s = Settings()
    s.venue, s.cost_realism = "demo", True
    s.exploration_mode, s.allowed_regimes, s.ensemble = False, [], []
    s.taker_fee_bp, s.maker_fee_bp, s.stop_slip_bp = 5.5, 2.0, 5.0
    return s


# ---------------------------------------------------------------- venue
def test_demo_venue_hosts_and_allowlist():
    from ctb.broker.bybit_private_ws import BybitPrivateStream
    from ctb.broker.bybit_rest import BybitClient

    c = BybitClient("k", "s", object(), "demo")  # type: ignore[arg-type]
    assert c.host == "https://api-demo.bybit.com"
    assert c.public_host == "https://api.bybit.com"  # market data from mainnet, unsigned
    assert BybitPrivateStream("k", "s", object(), None, "demo").url == "wss://stream-demo.bybit.com/v5/private"  # type: ignore[arg-type]
    assert BybitClient("k", "s", object()).host == "https://api-testnet.bybit.com"  # type: ignore[arg-type]
    # real-money hosts are never signed for
    assert "https://api.bybit.com" not in venue.PRIVATE_HOSTS
    with pytest.raises(ValueError):
        venue.get("mainnet")
    s = demo()
    assert s.shadow_costs and s.public_view()["venue_label"] == "BYBIT DEMO"
    s.venue = "testnet"
    assert not s.shadow_costs  # testnet fills are real fills on the book we trade


def test_venue_change_starts_a_new_run(tmp_path):
    from ctb import runs

    db = DB(tmp_path / "t.db")
    s = demo()
    s.venue = "testnet"
    r1 = runs.ensure(db, s)
    assert runs.ensure(db, s)["id"] == r1["id"]
    s.venue = "demo"
    r2 = runs.ensure(db, s)
    assert r2["id"] != r1["id"] and r2["config_json"]["venue"] == "demo" and "venue changed" in r2["notes"]


# ---------------------------------------------------------------- realism math
def test_walk_book_vwap_and_shallow_book():
    asks = [(100.0, 1.0), (100.1, 1.0), (100.5, 10.0)]
    w = realism.walk_book(asks, 2.0)
    assert w["vwap"] == pytest.approx(100.05) and w["short_qty"] == 0
    w = realism.walk_book(asks, 13.0)  # deeper than the book: rest at the last level, flagged
    assert w["short_qty"] == pytest.approx(1.0)


def test_extra_slip_takes_the_worse_of_venue_and_book():
    # demo filled a long at the touch, the real book walk says 100.05: charge the 5 bp gap
    x = realism.extra_slip("long", 2.0, 100.0, 100.0, 100.05, entry=True)
    assert x["venue_slip_bp"] == 0 and x["slip_bp"] == pytest.approx(5.0) and x["extra_usd"] == pytest.approx(0.1)
    # demo already slipped more than the model: book Bybit's number, charge nothing extra
    x = realism.extra_slip("long", 2.0, 100.0, 100.2, 100.05, entry=True)
    assert x["extra_usd"] == 0 and x["slip_bp"] == pytest.approx(20.0)
    # short exit (buy back) above the reference is adverse; a floor applies without a book
    x = realism.extra_slip("short", 1.0, 100.0, 100.0, None, entry=False, floor_bp=5.0)
    assert x["extra_usd"] == pytest.approx(0.05)


def test_book_close_charges_missing_fees_funding_and_stop_floor():
    s = demo()
    t = {"side": "long", "entry_price": 100.0, "entry_mode": "taker", "exit_mode": "managed", "opened_ts": 1000.0,
         "sl_price": 99.0, "realism_json": {"entry": {"extra_usd": 0.1}}}
    agg = {"size": 10.0, "exit_price": 99.0, "pnl": -10.0, "closed_ts": 5000.0}
    funding = [{"fundingRateTimestamp": "2000000", "fundingRate": "0.0001"},   # inside the hold: paid
               {"fundingRateTimestamp": "9000000", "fundingRate": "0.0001"}]   # after the close: ignored
    up = realism.book_close(t, agg, venue_fees=0.0, reason="stop_loss", s=s, funding_rows=funding, venue_exit_slip_bp=0.0)
    fees = (1000 * 5.5 + 990 * 5.5) / 10000           # taker both legs, Bybit charged nothing
    stop = 10 * 99.0 * 5.0 / 10000                    # exchange stop, no book snapshot: 5 bp floor
    fund = 10 * 99.0 * 0.0001
    assert up["venue_pnl"] == -10.0 and up["fees"] == pytest.approx(fees)
    assert up["funding_usd"] == pytest.approx(fund)
    assert up["realism_adj_usd"] == pytest.approx(0.1 + fees + stop + fund, abs=1e-6)
    assert up["realized_pnl"] == pytest.approx(-10.0 - up["realism_adj_usd"])
    assert up["exit_slip_bp"] == pytest.approx(5.0)
    # when Bybit already charges the schedule, the fee leg adds nothing
    up2 = realism.book_close({**t, "realism_json": {}}, agg, venue_fees=fees, reason="take_profit", s=s,
                             funding_rows=[], venue_exit_slip_bp=0.0)
    assert up2["realism_json"]["fees"]["maker_exit"] and up2["realism_json"]["fees"]["extra_usd"] == 0


def test_queue_check_through_touch_none():
    trades = [{"time": "10000", "price": "100.0"}, {"time": "11000", "price": "100.2"}]
    assert realism.queue_check(True, 100.0, trades, 9.0, 12.0) == "touch"     # resting buy at 100: only touched
    assert realism.queue_check(True, 100.1, trades, 9.0, 12.0) == "through"   # traded below 100.1
    assert realism.queue_check(True, 99.0, trades, 9.0, 12.0) == "none"
    bars = [[0, "100", "100.3", "100.0", "100.1"]]
    assert realism.queue_check_bars(False, 100.3, bars) == "touch" and realism.queue_check_bars(False, 100.2, bars) == "through"


# ---------------------------------------------------------------- executor end to end
class DemoClient:
    """Fake demo venue: books a losing stop with zero fees and a touch-perfect fill."""

    async def closed_pnl(self, limit=100, start_ms=None, symbol=None):
        return [{"orderId": "x1", "closedSize": "1", "avgEntryPrice": "100", "avgExitPrice": "99", "closedPnl": "-1",
                 "openFee": "0", "closeFee": "0", "createdTime": "1", "updatedTime": str(int(time.time() * 1000))}]

    async def order_history(self, symbol, order_link_id=None, order_id=None):
        return {"stopOrderType": "StopLoss", "createType": "CreateByStopLoss"}

    async def funding_history(self, *a, **k):
        return []


def test_record_close_rebooks_demo_trade(tmp_path):
    from ctb.broker.executor import Executor

    db = DB(tmp_path / "t.db")
    ex = Executor(demo(), db, DemoClient(), emit=lambda *a: None)  # type: ignore[arg-type]
    tid = db.insert("trades", {"symbol": "BTCUSDT", "side": "long", "status": "open", "qty": 1, "entry_price": 100.0,
                               "sl_price": 99.0, "tp_price": 101.5, "opened_ts": time.time() - 60, "entry_mode": "taker",
                               "exit_mode": "managed", "venue": "demo", "realism_json": {"entry": {"extra_usd": 0.02}}})
    assert asyncio.run(ex.record_close(db.one("SELECT * FROM trades WHERE id=?", (tid,))))
    t = db.one("SELECT * FROM trades WHERE id=?", (tid,))
    assert t["venue_pnl"] == -1.0 and t["realism_adj_usd"] > 0.1     # fees + stop floor + entry gap
    assert t["realized_pnl"] == pytest.approx(-1.0 - t["realism_adj_usd"])
    assert t["r_mult"] == pytest.approx(t["realized_pnl"] / 1.0)      # R from the booked P&L
    assert t["close_reason"] == "stop_loss" and t["fees"] > 0


# ---------------------------------------------------------------- UI-editable settings
def test_config_registry_validates_and_maps_to_env():
    from ctb.config import EDITABLE, config_env, config_view, parse_config

    s = demo()
    s.venue = "testnet"
    assert all(hasattr(s, k) for k in EDITABLE)  # every field exists on Settings
    assert parse_config(s, {"venue": "DEMO", "max_leverage": "5", "allow_short": "off"}) == {
        "venue": "demo", "max_leverage": 5, **({"allow_short": False} if s.allow_short else {})}
    assert parse_config(s, {"max_leverage": s.max_leverage}) == {}  # unchanged values are dropped
    for bad in ({"max_leverage": 2.5}, {"max_leverage": 99}, {"venue": "mainnet"}, {"taker_fee_bp": "x"}, {"bybit_api_key": "k"}):
        with pytest.raises(ValueError):
            parse_config(s, bad)
    assert config_env({"venue": "demo", "cost_realism": False, "max_leverage": 5}) == {
        "CTB_VENUE": "demo", "CTB_COST_REALISM": "0", "CTB_MAX_LEVERAGE": "5"}
    v = config_view(s)
    assert {f["k"] for f in v["fields"] if f["restart"]} == {"venue"} and v["values"]["venue"] == "testnet"


def test_bybit_keys_are_kept_per_venue(monkeypatch):
    from ctb.config import _load_bybit_keys

    for k in ("BYBIT_API_KEY", "BYBIT_API_SECRET", "BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET",
              "BYBIT_DEMO_API_KEY", "BYBIT_DEMO_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BYBIT_API_KEY", "legacy-k")  # the old pair is the testnet pair
    monkeypatch.setenv("BYBIT_API_SECRET", "legacy-s")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "demo-k")
    monkeypatch.setenv("BYBIT_DEMO_API_SECRET", "demo-s")
    s = demo()
    s.bybit_keys = _load_bybit_keys()
    assert (s.bybit_api_key, s.bybit_api_secret) == ("demo-k", "demo-s")
    s.venue = "testnet"
    assert s.bybit_api_key == "legacy-k"
