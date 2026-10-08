"""SQLite storage for the trade journal and AI decision history."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

from core.paths import DB_FILE, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    engine_state TEXT,
    mt5_status TEXT,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT,
    ticket INTEGER,
    symbol TEXT,
    side TEXT,
    lot REAL,
    open_ts REAL,
    open_price REAL,
    close_ts REAL,
    close_price REAL,
    profit REAL,
    reason_open TEXT,
    reason_close TEXT,
    payload TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    level TEXT,
    source TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_open_ts ON trades(open_ts);
CREATE INDEX IF NOT EXISTS idx_cycles_ts ON cycles(ts);
"""


class Storage:
    def __init__(self, path=None):
        ensure_dirs()
        self._path = str(path or DB_FILE)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------ #
    def save_cycle(self, cycle_id: str, ts: float, engine_state: str,
                   mt5_status: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cycles (id, ts, engine_state, "
                "mt5_status, payload) VALUES (?, ?, ?, ?, ?)",
                (cycle_id, ts, engine_state, mt5_status,
                 json.dumps(payload, ensure_ascii=False, default=str)))
            self._conn.commit()

    def recent_cycles(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, engine_state, mt5_status, payload FROM "
                "cycles ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            try:
                payload = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            out.append({"id": r["id"], "ts": r["ts"],
                        "engine_state": r["engine_state"],
                        "mt5_status": r["mt5_status"], "payload": payload})
        return out

    # ------------------------------------------------------------------ #
    def trade_open(self, cycle_id: str, ticket: Optional[int], symbol: str,
                   side: str, lot: float, open_ts: float, open_price: float,
                   reason_open: str, payload: Dict[str, Any]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO trades (cycle_id, ticket, symbol, side, lot, "
                "open_ts, open_price, reason_open, payload) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cycle_id, ticket, symbol, side, lot, open_ts, open_price,
                 reason_open,
                 json.dumps(payload, ensure_ascii=False, default=str)))
            self._conn.commit()
            return int(cur.lastrowid)

    def trade_close(self, ticket: Optional[int], db_id: Optional[int],
                    close_ts: float, close_price: Optional[float],
                    profit: float, reason_close: str) -> None:
        with self._lock:
            if db_id:
                self._conn.execute(
                    "UPDATE trades SET close_ts=?, close_price=?, profit=?, "
                    "reason_close=? WHERE id=?",
                    (close_ts, close_price, profit, reason_close, db_id))
            else:
                self._conn.execute(
                    "UPDATE trades SET close_ts=?, close_price=?, profit=?, "
                    "reason_close=? WHERE ticket=? AND close_ts IS NULL",
                    (close_ts, close_price, profit, reason_close, ticket))
            self._conn.commit()

    def find_open_trade(self, ticket: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trades WHERE ticket=? AND close_ts IS NULL "
                "ORDER BY id DESC LIMIT 1", (ticket,)).fetchone()
        return dict(row) if row else None

    def recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def trades_since(self, ts: float) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE open_ts >= ?", (ts,)).fetchall()
        return [dict(r) for r in rows]

    def log_event(self, level: str, source: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, level, source, message) VALUES "
                "(?, ?, ?, ?)", (time.time(), level, source, message[:1000]))
            self._conn.commit()

    def recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, level, source, message FROM events ORDER BY id "
                "DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
