"""SQLite persistence. Every Kairos request/answer, label, trade and event is kept so the
evaluation can be recomputed at any time from raw records."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  symbol TEXT NOT NULL,
  bar_time INTEGER,
  qset_version TEXT,
  probe TEXT,              -- NULL for live decisions; 'mirror' | 'repeat' for consistency probes
  probe_of INTEGER,
  state_json TEXT,
  questions_json TEXT,
  answers_json TEXT,
  parsed_json TEXT,
  issues_json TEXT,
  usage_json TEXT,
  model_meta_json TEXT,
  latency_ms REAL,
  attempts INTEGER,
  request_bytes INTEGER,
  error_kind TEXT,
  error_msg TEXT,
  request_id TEXT,
  decision_json TEXT,
  action TEXT,
  price REAL,
  testnet_price REAL,
  sl_bp REAL,
  tp_bp REAL,
  horizon_min INTEGER,
  label_json TEXT,
  labeled_at REAL
);
CREATE INDEX IF NOT EXISTS ix_dec_symbol_ts ON decisions(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_dec_unlabeled ON decisions(labeled_at) WHERE labeled_at IS NULL;

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id INTEGER,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL,
  notional REAL,
  entry_link_id TEXT,
  entry_order_id TEXT,
  entry_price REAL,
  tp_price REAL,
  sl_price REAL,
  protection TEXT,
  opened_ts REAL,
  deadline_ts REAL,
  status TEXT,             -- open | closed | error
  close_reason TEXT,
  closed_ts REAL,
  exit_price REAL,
  realized_pnl REAL,
  fees REAL,
  ev_r REAL,
  risk_usd REAL,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS news (
  id TEXT PRIMARY KEY,
  ts REAL,
  symbol TEXT,
  source TEXT,
  title TEXT,
  published_at TEXT,
  horizon TEXT,
  cost_bp REAL,
  entry_ts REAL,
  probs_json TEXT,
  issues_json TEXT,
  usage_json TEXT,
  latency_ms REAL,
  error_kind TEXT,
  error_msg TEXT,
  request_id TEXT,
  label_json TEXT,
  labeled_at REAL
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  started_ts REAL,
  ended_ts REAL,
  notes TEXT,
  config_json TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  level TEXT,
  kind TEXT,
  symbol TEXT,
  message TEXT
);
"""

# Execution-accuracy columns added after the first release (migrated in place).
TRADE_COLUMNS = {
    "mode": "TEXT",              # strict | exploration
    "requested_qty": "REAL",     # what we asked for; qty is what Bybit actually filled
    "ref_price": "REAL",         # best testnet price at order time
    "cap_price": "REAL",         # IOC limit price (ref +/- max_entry_slip_bp)
    "decision_price": "REAL",    # mainnet price the decision was made on
    "book_spread_bp": "REAL",
    "planned_sl_bp": "REAL",
    "planned_tp_bp": "REAL",
    "entry_fee": "REAL",
    "exit_fee": "REAL",
    "entry_slip_bp": "REAL",     # + = filled worse than ref
    "exit_slip_bp": "REAL",      # + = exited worse than the TP/SL trigger
    "entry_latency_ms": "REAL",  # order sent -> fill confirmed
    "r_mult": "REAL",            # realized_pnl / (qty x |entry - live stop|)
    "exit_fills": "INTEGER",
    "fills_json": "TEXT",
    "close_notes": "TEXT",
    "exit_mode": "TEXT",         # managed | exchange
    "tp_order_id": "TEXT",       # resting reduce-only limit at the target (managed mode)
    "backstop_price": "REAL",    # exchange-side stop kept beyond the managed stop
    "stop_trigger_px": "REAL",   # testnet bid/ask that tripped the managed stop
    "stop_trigger_ts": "REAL",
    "exit_steps": "INTEGER",     # IOC chase steps used to get flat
    "entry_mode": "TEXT",        # maker (post-only limit) | taker (capped IOC)
    "entry_wait_ms": "REAL",     # maker: order placed -> filled or cancelled
    "sizing_json": "TEXT",       # how the risk amount was set (base, confidence, regime, drawdown, correlation)
    "horizon_min": "INTEGER",    # holding limit of this trade (brackets can extend the horizon)
    "be_moved_ts": "REAL",       # when the managed stop moved to break-even
    "mfe_r": "REAL",             # best excursion in R seen while open (mainnet)
    "regime": "TEXT",
    "events_json": "TEXT",       # market events on the decision bar
}

JSON_COLS = {"state_json", "questions_json", "answers_json", "parsed_json", "issues_json", "usage_json",
             "model_meta_json", "decision_json", "label_json", "probs_json", "fills_json", "config_json",
             "sizing_json", "events_json"}


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.Lock()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(decisions)")}
        if "run_id" not in cols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN run_id INTEGER")
        if "model_provider" not in cols:
            # which classification model answered; everything before multi-model support was Kairos
            self.conn.execute("ALTER TABLE decisions ADD COLUMN model_provider TEXT")
            self.conn.execute("UPDATE decisions SET model_provider='kairos' WHERE model_provider IS NULL")
        tcols = {r[1] for r in self.conn.execute("PRAGMA table_info(trades)")}
        if "run_id" not in tcols:
            self.conn.execute("ALTER TABLE trades ADD COLUMN run_id INTEGER")
        for col, typ in TRADE_COLUMNS.items():
            if col not in tcols:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        self.conn.execute("UPDATE trades SET mode=notes WHERE mode IS NULL AND notes IN ('exploration','strict')")
        self.conn.execute("UPDATE trades SET entry_mode='taker' WHERE entry_mode IS NULL")
        if "decision_tf" not in cols:
            self.conn.execute("ALTER TABLE decisions ADD COLUMN decision_tf TEXT")
            # every decision before this column existed was made on the 5m profile (120 min horizon)
            self.conn.execute("UPDATE decisions SET decision_tf='5' WHERE decision_tf IS NULL AND horizon_min=120")

    @staticmethod
    def _enc(row: dict[str, Any]) -> dict[str, Any]:
        return {k: (json.dumps(v, separators=(",", ":"), default=str) if k in JSON_COLS and v is not None else v) for k, v in row.items()}

    @staticmethod
    def _dec(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for k in JSON_COLS & out.keys():
            if out[k] is not None:
                out[k] = json.loads(out[k])
        return out

    def insert(self, table: str, row: dict[str, Any]) -> int:
        row = self._enc(row)
        cols = ",".join(row)
        q = ",".join("?" for _ in row)
        with self.lock:
            cur = self.conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({q})", list(row.values()))
            return int(cur.lastrowid)

    def update(self, table: str, key: Any, row: dict[str, Any], key_col: str = "id") -> None:
        row = self._enc(row)
        sets = ",".join(f"{k}=?" for k in row)
        with self.lock:
            self.conn.execute(f"UPDATE {table} SET {sets} WHERE {key_col}=?", [*row.values(), key])

    def query(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        return [self._dec(r) for r in rows]  # type: ignore[misc]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def event(self, level: str, kind: str, message: str, symbol: str | None = None) -> dict[str, Any]:
        ev = {"ts": time.time(), "level": level, "kind": kind, "symbol": symbol, "message": message[:1000]}
        self.insert("events", ev)
        return ev
