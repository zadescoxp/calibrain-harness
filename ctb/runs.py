"""Test runs: named, time-bounded sessions with the configuration they ran under.

Every decision and trade is stamped with the run that was active when it was made, so each run's
model quality and P&L can be read on its own (Run 1 vs Run 2 vs Run 3) next to the overall total.
A run stays open across restarts (a symbol change restarts the service) until a new run starts.
"""

from __future__ import annotations

import time
from typing import Any

from .config import Settings
from .db import DB


def config_snapshot(s: Settings) -> dict[str, Any]:
    from .kairos.questions import QUESTION_SET_VERSION

    return {
        "model_provider": s.model_provider, "model_id": s.model_spec.model,
        "symbols": s.symbols, "decision_tf": s.decision_tf, "horizon_minutes": s.horizon_bars * s.decision_tf_minutes,
        "qset": QUESTION_SET_VERSION, "exploration_mode": s.exploration_mode, "exit_mode": s.exit_mode,
        "risk_per_trade_pct": s.risk_per_trade_pct, "explore_risk_pct": s.explore_risk_pct, "max_positions": s.max_positions,
        "min_edge_r": s.min_edge_r, "max_entry_slip_bp": s.max_entry_slip_bp,
        "strategy": s.strategy_view(),
    }


def current(db: DB) -> dict[str, Any] | None:
    return db.one("SELECT * FROM runs WHERE ended_ts IS NULL ORDER BY id DESC LIMIT 1")


def start(db: DB, s: Settings, name: str | None = None, notes: str | None = None) -> dict[str, Any]:
    """End the open run (if any) and start a new one with the current configuration."""
    now = time.time()
    db.conn.execute("UPDATE runs SET ended_ts=? WHERE ended_ts IS NULL", (now,))
    n = (db.one("SELECT COUNT(*) n FROM runs") or {}).get("n", 0) + 1
    cfg = config_snapshot(s)
    st = cfg["strategy"]
    auto = (f"Run {n} · {s.model_spec.label} · {cfg['qset']} · {'explore' if cfg['exploration_mode'] else 'strict'} · "
            f"{st['bracket_mode']} bracket · {st['entry_mode']} · {'sym' if st['symmetrize'] else 'raw'} · {st['trade_trigger']} · "
            f"{len(cfg['symbols'])} symbols")
    rid = db.insert("runs", {"name": name or auto, "started_ts": now, "ended_ts": None, "notes": notes, "config_json": cfg})
    return db.one("SELECT * FROM runs WHERE id=?", (rid,))


def ensure(db: DB, s: Settings) -> dict[str, Any]:
    return current(db) or start(db, s)


def define_history(db: DB, boundaries: list[tuple[str, float, float | None, dict[str, Any], str]]) -> None:
    """Create runs for data recorded before runs existed and stamp their decisions and trades.
    boundaries: (name, started_ts, ended_ts or None, config, notes)."""
    for name, t0, t1, cfg, notes in boundaries:
        rid = db.insert("runs", {"name": name, "started_ts": t0, "ended_ts": t1, "notes": notes, "config_json": cfg})
        end = t1 if t1 is not None else 1e18
        db.conn.execute("UPDATE decisions SET run_id=? WHERE run_id IS NULL AND ts>=? AND ts<?", (rid, t0, end))
        db.conn.execute("UPDATE trades SET run_id=? WHERE run_id IS NULL AND opened_ts>=? AND opened_ts<?", (rid, t0, end))
