"""Label every Kairos prediction with what the market actually did (mainnet 1m bars).

Labels are computed for ALL decisions (traded or not) so model quality is measured on an
unbiased sample, separately from execution quality.

Conventions (documented so Minara can reproduce them):
- Entry = decision price (mainnet last at decision time). The path starts at the first 1m
  bar that opens at or after the decision timestamp (no look-ahead into the decision minute).
- Bracket: a bar touching target and stop together is ambiguous; it is counted as
  stop_first (conservative) and flagged.
- Return at horizon = close of the last 1m bar inside the horizon vs entry.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from ..db import DB
from ..kairos import news as news_profile
from ..kairos.questions import RETURN_BIN_EDGES_IN_D

log = logging.getLogger("ctb.labeler")
MAINNET_REST = "https://api.bybit.com"


async def fetch_1m(session: aiohttp.ClientSession, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float, float, float, float]]:
    out: dict[int, tuple] = {}
    cursor = start_ms
    while cursor < end_ms:
        params = {"category": "linear", "symbol": symbol, "interval": "1", "start": cursor, "end": end_ms, "limit": 1000}
        async with session.get(f"{MAINNET_REST}/v5/market/kline", params=params, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json(content_type=None)
        rows = data.get("result", {}).get("list", [])
        if not rows:
            break
        for row in rows:
            t = int(row[0])
            out[t] = (t, float(row[1]), float(row[2]), float(row[3]), float(row[4]))
        newest = max(int(r[0]) for r in rows)
        if newest + 60000 <= cursor:
            break
        cursor = newest + 60000
        if len(rows) < 1000:
            break
    return [out[k] for k in sorted(out)]


def bracket_outcome(bars: list[tuple], entry: float, side: str, tp_bp: float, sl_bp: float) -> dict[str, Any]:
    sign = 1 if side == "long" else -1
    tp = entry * (1 + sign * tp_bp / 10000)
    sl = entry * (1 - sign * sl_bp / 10000)
    for i, (_, _o, h, l, _c) in enumerate(bars):
        hit_tp = h >= tp if side == "long" else l <= tp
        hit_sl = l <= sl if side == "long" else h >= sl
        if hit_tp and hit_sl:
            return {"outcome": "stop_first", "bars": i + 1, "ambiguous": True}
        if hit_sl:
            return {"outcome": "stop_first", "bars": i + 1, "ambiguous": False}
        if hit_tp:
            return {"outcome": "target_first", "bars": i + 1, "ambiguous": False}
    return {"outcome": "neither", "bars": len(bars), "ambiguous": False}


def return_bin(ret_pct: float, sl_bp: float) -> int:
    d = sl_bp / 100
    for i, e in enumerate(RETURN_BIN_EDGES_IN_D):
        if ret_pct < e * d:
            return i
    return 6


def realized_r(outcome: str, rr: float, ret_pct: float, sl_bp: float, side: str, cost_bp: Any) -> float:
    """Hypothetical bracket result in R, net of cost. cost_bp is one flat round-trip cost, or the
    per-outcome cost model of the decision (costs.CostModel.outcome_costs)."""
    from ..costs import outcome_cost_bp
    cost_r = outcome_cost_bp(outcome, cost_bp) / sl_bp
    if outcome == "target_first":
        return rr - cost_r
    if outcome == "stop_first":
        return -1 - cost_r
    sign = 1 if side == "long" else -1
    return sign * ret_pct * 100 / sl_bp - cost_r


def label_decision(d: dict[str, Any], bars: list[tuple]) -> dict[str, Any] | None:
    ts_ms = int(d["ts"] * 1000)
    start = ts_ms - ts_ms % 60000 + (60000 if ts_ms % 60000 else 0)
    end = start + d["horizon_min"] * 60000
    path = [b for b in bars if start <= b[0] < end]
    if len(path) < d["horizon_min"] * 0.9:
        return None
    entry, sl_bp, tp_bp = d["price"], d["sl_bp"], d["tp_bp"]
    rr = tp_bp / sl_bp
    dj = d.get("decision_json") or {}
    cost_bp = ((dj.get("meta") or {}).get("costs") or dj.get("cost_bp")
               or (d.get("state_json") or {}).get("trade_plan", {}).get("round_trip_cost_pct", 0.15) * 100)
    long_o = bracket_outcome(path, entry, "long", tp_bp, sl_bp)
    short_o = bracket_outcome(path, entry, "short", tp_bp, sl_bp)
    ret_pct = 100 * (path[-1][4] / entry - 1)
    mfe = 100 * (max(b[2] for b in path) / entry - 1)
    mae = 100 * (min(b[3] for b in path) / entry - 1)
    label: dict[str, Any] = {
        "long": long_o, "short": short_o, "return_pct": round(ret_pct, 4), "return_bin": return_bin(ret_pct, sl_bp),
        "max_up_pct": round(mfe, 4), "max_down_pct": round(mae, 4), "bars": len(path),
        # price change k minutes after entry (bp, side-neutral): execution markouts and short-horizon edge
        "markout_bp": {str(k): round(10000 * (path[k - 1][4] / entry - 1), 2) for k in (1, 5, 15) if len(path) >= k},
        "long_r": round(realized_r(long_o["outcome"], rr, ret_pct, sl_bp, "long", cost_bp), 4),
        "short_r": round(realized_r(short_o["outcome"], rr, ret_pct, sl_bp, "short", cost_bp), 4),
    }
    # mean reversion toward 1h EMA20 (EMA level held fixed over the horizon: it moves slowly)
    pve = (((d.get("state_json") or {}).get("trend_by_timeframe") or {}).get("1h") or {}).get("price_vs_ema20_pct")
    if pve is not None:
        ema = entry / (1 + pve / 100)
        label["reverted_to_ema"] = abs(path[-1][4] - ema) < abs(entry - ema)
    # heuristic regime label
    d_pct = sl_bp / 100
    touched_up, touched_dn = mfe >= d_pct, -mae >= d_pct
    if touched_up and touched_dn:
        label["regime"] = "volatile_chop"
    elif ret_pct >= d_pct and touched_up:
        label["regime"] = "uptrend"
    elif ret_pct <= -d_pct and touched_dn:
        label["regime"] = "downtrend"
    else:
        label["regime"] = "range"
    return label


def label_news(n: dict[str, Any], bars: list[tuple]) -> dict[str, Any] | None:
    mins = {"15m": 15, "1h": 60, "4h": 240}[n["horizon"]]
    entry_ms = int(n["entry_ts"] * 1000)
    start = entry_ms - entry_ms % 60000 + 60000  # first bar open >= seen + 60s
    path = [b for b in bars if start <= b[0] <= start + mins * 60000]
    if len(path) < mins * 0.9:
        return None
    o0 = path[0][1]
    o1 = path[-1][1]  # open-to-open
    ret_bp = 10000 * (o1 / o0 - 1)
    return {"return_bp": round(ret_bp, 2), "bin": news_profile.bin_index(ret_bp, n["cost_bp"])}


class Labeler:
    def __init__(self, db: DB, session: aiohttp.ClientSession, emit):
        self.db = db
        self.session = session
        self.emit = emit
        self.labeled = 0

    async def loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.warning("labeler error: %s", exc)
            await asyncio.sleep(60)

    async def run_once(self) -> int:
        now = time.time()
        pending = self.db.query(
            "SELECT id, ts, symbol, price, sl_bp, tp_bp, horizon_min, state_json, decision_json FROM decisions "
            "WHERE labeled_at IS NULL AND error_kind IS NULL AND price IS NOT NULL AND ts + horizon_min*60 + 90 < ? LIMIT 400", (now,))
        n = 0
        by_sym: dict[str, list[dict]] = {}
        for d in pending:
            by_sym.setdefault(d["symbol"], []).append(d)
        for sym, ds in by_sym.items():
            start = int(min(d["ts"] for d in ds) * 1000) - 60000
            end = int(max(d["ts"] + d["horizon_min"] * 60 for d in ds) * 1000) + 120000
            bars = await fetch_1m(self.session, sym, start, end)
            for d in ds:
                lab = label_decision(d, bars)
                if lab is None:
                    if now - d["ts"] > 3 * 86400:  # give up on very old gaps
                        self.db.update("decisions", d["id"], {"labeled_at": now, "label_json": {"unlabelable": True}})
                    continue
                self.db.update("decisions", d["id"], {"label_json": lab, "labeled_at": now})
                n += 1
        news_pending = self.db.query(
            "SELECT * FROM news WHERE labeled_at IS NULL AND probs_json IS NOT NULL AND entry_ts + "
            "(CASE horizon WHEN '15m' THEN 900 WHEN '1h' THEN 3600 ELSE 14400 END) + 180 < ?", (now,))
        for nw in news_pending:
            mins = {"15m": 15, "1h": 60, "4h": 240}[nw["horizon"]]
            bars = await fetch_1m(self.session, nw["symbol"], int(nw["entry_ts"] * 1000), int(nw["entry_ts"] * 1000) + (mins + 3) * 60000)
            lab = label_news(nw, bars)
            if lab:
                self.db.update("news", nw["id"], {"label_json": lab, "labeled_at": now}, key_col="id")
                n += 1
        if n:
            self.labeled += n
            self.emit("labels", {"count": n})
        return n
