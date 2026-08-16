#!/usr/bin/env python3
"""
SQLite memory foundation for PiBot.

Lightweight, optional persistence for:
- user preferences
- memories (summaries)
- observations (vision notes)
- events (system logs)

Safe to delete: the DB is treated as cacheable memory, not core config.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_DB_PATH = os.getenv("PIBOT_DB_PATH", "data/pibot.db")
SCHEMA_VERSION = 2
DEFAULT_LIMITS = {
    "memories": 1000,
    "observations": 1000,
    "events": 5000,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str]) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _ensure_dir(path: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterable[sqlite3.Connection]:
    _ensure_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        _apply_schema(conn)


def _apply_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            summary TEXT NOT NULL,
            tags TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories (created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            image_path TEXT,
            summary TEXT NOT NULL,
            tags TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_observations_created_at ON observations (created_at)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at)")
    _set_schema_version(conn, SCHEMA_VERSION)


def _get_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    if not row:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(version),),
    )


def ensure_schema(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        current = _get_schema_version(conn)
        if current < SCHEMA_VERSION:
            _apply_schema(conn)


def set_preference(key: str, value: Any, db_path: str = DEFAULT_DB_PATH) -> None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO preferences (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, _json_dumps(value), _utc_now()),
        )


def get_preference(key: str, default: Any = None, db_path: str = DEFAULT_DB_PATH) -> Any:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return _json_loads(row["value"])


def get_preferences(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM preferences ORDER BY key").fetchall()
        return {row["key"]: _json_loads(row["value"]) for row in rows}


def add_memory(summary: str, source: str = "voice", tags: Optional[List[str]] = None,
               db_path: str = DEFAULT_DB_PATH) -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO memories (created_at, source, summary, tags) VALUES (?, ?, ?, ?)",
            (_utc_now(), source, summary, _json_dumps(tags)),
        )
        return int(cursor.lastrowid)


def add_observation(summary: str, image_path: Optional[str] = None,
                    tags: Optional[List[str]] = None, db_path: str = DEFAULT_DB_PATH) -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO observations (created_at, image_path, summary, tags) VALUES (?, ?, ?, ?)",
            (_utc_now(), image_path, summary, _json_dumps(tags)),
        )
        return int(cursor.lastrowid)


def log_event(event_type: str, data: Optional[Dict[str, Any]] = None,
              db_path: str = DEFAULT_DB_PATH) -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO events (created_at, event_type, data) VALUES (?, ?, ?)",
            (_utc_now(), event_type, _json_dumps(data)),
        )
        return int(cursor.lastrowid)


def get_recent_memories(limit: int = 10, db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_recent_events(limit: int = 20, event_type: Optional[str] = None,
                      db_path: str = DEFAULT_DB_PATH) -> List[Dict[str, Any]]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def stats(db_path: str = DEFAULT_DB_PATH) -> Dict[str, int]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        counts = {}
        for table in ("preferences", "memories", "observations", "events"):
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int(row["count"]) if row else 0
        return counts


def _limit_from_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def retention_limits() -> Dict[str, int]:
    return {
        "memories": _limit_from_env("PIBOT_DB_LIMIT_MEMORIES", DEFAULT_LIMITS["memories"]),
        "observations": _limit_from_env("PIBOT_DB_LIMIT_OBSERVATIONS", DEFAULT_LIMITS["observations"]),
        "events": _limit_from_env("PIBOT_DB_LIMIT_EVENTS", DEFAULT_LIMITS["events"]),
    }


def _prune_table(conn: sqlite3.Connection, table: str, limit: int) -> int:
    if limit <= 0:
        return 0
    before = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
    conn.execute(
        f"""
        DELETE FROM {table}
        WHERE id NOT IN (
            SELECT id FROM {table}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
        )
        """,
        (limit,),
    )
    after = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
    return max(0, int(before) - int(after))


def prune_retention(db_path: str = DEFAULT_DB_PATH) -> Dict[str, int]:
    ensure_schema(db_path)
    limits = retention_limits()
    deleted: Dict[str, int] = {}
    with connect(db_path) as conn:
        for table, limit in limits.items():
            deleted[table] = _prune_table(conn, table, limit)
    return deleted


def check_integrity(db_path: str = DEFAULT_DB_PATH) -> str:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
        return row[0] if row else "unknown"


def maintain(db_path: str = DEFAULT_DB_PATH) -> Dict[str, Any]:
    deleted = prune_retention(db_path)
    integrity = check_integrity(db_path)
    return {"deleted": deleted, "integrity": integrity}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PiBot SQLite memory DB utilities")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    parser.add_argument("--init", action="store_true", help="Initialize schema")
    parser.add_argument("--stats", action="store_true", help="Print row counts")
    parser.add_argument("--integrity", action="store_true", help="Run integrity check")
    parser.add_argument("--prune", action="store_true", help="Apply retention limits")
    parser.add_argument("--maintain", action="store_true", help="Run prune + integrity")
    args = parser.parse_args()

    if args.init or args.stats or args.integrity or args.prune or args.maintain:
        ensure_schema(args.db)

    if args.stats:
        print(json.dumps(stats(args.db), indent=2))
    if args.integrity:
        print(f"integrity: {check_integrity(args.db)}")
    if args.prune:
        print(json.dumps({"deleted": prune_retention(args.db)}, indent=2))
    if args.maintain:
        print(json.dumps(maintain(args.db), indent=2))
    if args.init:
        print(f"Initialized DB at {args.db}")


if __name__ == "__main__":
    main()
