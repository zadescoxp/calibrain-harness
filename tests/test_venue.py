"""Order venues: testnet or Bybit Demo Trading, never mainnet."""

import pytest


def test_venue_never_signs_for_mainnet(monkeypatch):
    from ctb import venue
    from ctb.broker.bybit_rest import BybitTestnet
    monkeypatch.delenv("CTB_VENUE", raising=False)
    assert venue.get()["rest"] == "https://api-testnet.bybit.com"
    monkeypatch.setenv("CTB_VENUE", "demo")
    v = venue.get()
    assert v["rest"] == "https://api-demo.bybit.com"
    assert v["private_ws"] == "wss://stream-demo.bybit.com/v5/private"
    assert v["public_ws"].startswith("wss://stream.bybit.com/")
    assert all(x["rest"] in venue.TRADING_HOSTS for x in venue.VENUES.values())
    assert "https://api.bybit.com" not in venue.TRADING_HOSTS
    assert BybitTestnet("k", "s", None).host == "https://api-demo.bybit.com"
    for bad in ("mainnet", "live", "prod"):
        monkeypatch.setenv("CTB_VENUE", bad)
        with pytest.raises(ValueError):
            venue.get()


def test_demo_venue_can_trade(monkeypatch):
    from ctb import venue
    monkeypatch.setenv("CTB_VENUE", "demo")
    assert venue.orders_blocked() is None


def test_demo_closed_rows_rebuilt_from_orders():
    from ctb.broker.executor import aggregate_close_rows, closed_rows_from_orders
    t = {"side": "long", "qty": 1.0, "entry_price": 100.0, "entry_fee": 0.055, "opened_ts": 1000.0, "entry_link_id": "ctb-1-a"}
    orders = [
        {"orderId": "e", "orderLinkId": "ctb-1-a", "side": "Buy", "cumExecQty": "1", "avgPrice": "100", "cumExecFee": "0.055", "createdTime": "1000000"},
        {"orderId": "tp1", "orderLinkId": "ctb-tp-1", "side": "Sell", "reduceOnly": True, "cumExecQty": "0.4", "avgPrice": "102",
         "cumExecFee": "0.0224", "createdTime": "1005000", "updatedTime": "1006000"},
        {"orderId": "sl", "orderLinkId": "", "side": "Sell", "reduceOnly": True, "stopOrderType": "StopLoss", "cumExecQty": "0.6",
         "avgPrice": "99", "cumExecFee": "0.0327", "createdTime": "1007000", "updatedTime": "1008000"},
        {"orderId": "cancelled", "side": "Sell", "reduceOnly": True, "cumExecQty": "0", "avgPrice": "", "createdTime": "1009000"},
        {"orderId": "old", "side": "Sell", "reduceOnly": True, "cumExecQty": "1", "avgPrice": "90", "createdTime": "900000"},
    ]
    rows = closed_rows_from_orders(t, orders)
    assert [r["orderId"] for r in rows] == ["tp1", "sl"]
    agg = aggregate_close_rows(rows, 1.0)
    assert agg["complete"]
    gross = 0.4 * 2 + 0.6 * -1
    assert agg["pnl"] == pytest.approx(gross - 0.055 - 0.0224 - 0.0327)
    assert agg["exit_price"] == pytest.approx(0.4 * 102 + 0.6 * 99)
    assert agg["open_fee"] == pytest.approx(0.055)
    short = {**t, "side": "short"}
    srows = closed_rows_from_orders(short, [{**orders[1], "side": "Buy"}])
    assert srows[0]["closedPnl"] == pytest.approx(0.4 * -2 - 0.055 * 0.4 - 0.0224)


def test_each_venue_reads_its_own_keys(monkeypatch):
    from ctb import venue
    from ctb.config import Settings
    monkeypatch.setenv("BYBIT_API_KEY", "tk")
    monkeypatch.setenv("BYBIT_API_SECRET", "ts")
    monkeypatch.setenv("BYBIT_DEMO_API_KEY", "dk")
    monkeypatch.delenv("BYBIT_DEMO_API_SECRET", raising=False)
    assert venue.keys("testnet") == ("tk", "ts")
    assert venue.keys("demo") == ("dk", "")
    s = Settings()
    assert (s.bybit_api_key, s.bybit_api_secret) == ("tk", "ts")
    monkeypatch.setenv("CTB_VENUE", "demo")
    s = Settings()
    assert (s.bybit_api_key, s.bybit_api_secret) == ("dk", "")


class _OneRow:
    def __init__(self, row):
        self.row = row

    def one(self, *_):
        return self.row


def test_venue_switch_refuses_unknown_venues_and_open_trades():
    import asyncio
    from types import SimpleNamespace

    from ctb.engine import Engine
    eng = SimpleNamespace(db=_OneRow({"id": 7}))
    with pytest.raises(ValueError, match="unknown venue"):
        asyncio.run(Engine.set_venue(eng, "mainnet"))
    assert asyncio.run(Engine.set_venue(eng, "testnet"))["unchanged"]
    with pytest.raises(ValueError, match="open bot trades"):
        asyncio.run(Engine.set_venue(eng, "demo"))
