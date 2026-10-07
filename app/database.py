"""SQLite storage: schema, connections and one-shot transaction helpers.

WAL + a single deferred read transaction guarantees that a query is evaluated
against one consistent snapshot (points and zones), even if another client
writes while the query runs.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
    zone_code      TEXT PRIMARY KEY,
    outer_ring     TEXT NOT NULL,
    holes          TEXT NOT NULL,
    valid_from_us  INTEGER NOT NULL,
    valid_to_us    INTEGER NOT NULL,
    created_at_us  INTEGER NOT NULL,
    updated_at_us  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS points (
    event_id        TEXT PRIMARY KEY,
    track_no        TEXT NOT NULL,
    x               REAL NOT NULL,
    y               REAL NOT NULL,
    sampled_at_us   INTEGER NOT NULL,
    created_at_us   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_points_track_time
    ON points (track_no, sampled_at_us);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    settings = get_settings()
    path = db_path or settings.db_path
    conn = sqlite3.connect(path, timeout=settings.busy_timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create the database file and schema (idempotent) and enable WAL."""
    settings = get_settings()
    path = db_path or settings.db_path
    parent = path.rsplit("/", 1)[0]
    if parent:
        import os

        os.makedirs(parent, exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE so validation reads and the write share one locked tx."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Open a deferred transaction; SQLite pins one read snapshot until commit."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.rollback()
