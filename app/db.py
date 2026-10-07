"""SQLite 持久化：禁入区与定位点。

- 写入使用 BEGIN IMMEDIATE 串行化，保证"查重再插入"的原子性；
- 读取在单个事务中完成，WAL 模式下同一事务始终看到同一快照。
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS zones (
    zone_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    outer_ring  TEXT NOT NULL,          -- JSON [[x,y], ...]
    holes       TEXT NOT NULL,          -- JSON [[[x,y],...], ...]
    valid_from  REAL NOT NULL,          -- Unix epoch 秒（UTC）
    valid_until REAL NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS points (
    event_id     TEXT PRIMARY KEY,      -- 事件号：重传幂等键
    trajectory_id TEXT NOT NULL,
    x            REAL NOT NULL,
    y            REAL NOT NULL,
    ts           REAL NOT NULL,         -- Unix epoch 秒（UTC）
    received_at  REAL NOT NULL,
    UNIQUE(trajectory_id, ts)           -- 同轨迹同时刻异坐标 => 拒绝
);

CREATE INDEX IF NOT EXISTS idx_points_traj_ts ON points(trajectory_id, ts);
"""


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def connect(db_path: str = "") -> sqlite3.Connection:
    if not db_path:
        db_path = os.environ.get("PATROL_DB_PATH", "data/patrol.db")
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """串行化写事务：BEGIN IMMEDIATE，异常回滚。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """只读快照事务（deferred BEGIN 即取共享快照，WAL 下写者不影响本事务）。"""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.rollback()


# ---------- zones ----------

def insert_zone(
    conn: sqlite3.Connection,
    outer: list,
    holes: list,
    valid_from: float,
    valid_until: float,
    name: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO zones(name, outer_ring, holes, valid_from, valid_until, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (name, json.dumps(outer), json.dumps(holes), valid_from, valid_until, _now()),
    )
    return int(cur.lastrowid)


def list_zones(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM zones ORDER BY zone_id"))


def get_zone(conn: sqlite3.Connection, zone_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM zones WHERE zone_id=?", (zone_id,)).fetchone()


# ---------- points ----------

def find_point_by_event(conn: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM points WHERE event_id=?", (event_id,)).fetchone()


def find_point_at_ts(
    conn: sqlite3.Connection, trajectory_id: str, ts: float
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM points WHERE trajectory_id=? AND abs(ts-?)<?",
        (trajectory_id, ts, 1e-6),
    ).fetchone()


def insert_point(
    conn: sqlite3.Connection,
    event_id: str,
    trajectory_id: str,
    x: float,
    y: float,
    ts: float,
) -> None:
    conn.execute(
        "INSERT INTO points(event_id, trajectory_id, x, y, ts, received_at)"
        " VALUES (?,?,?,?,?,?)",
        (event_id, trajectory_id, x, y, ts, _now()),
    )


def list_trajectories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT trajectory_id, COUNT(*) AS n_points, MIN(ts) AS first_ts, MAX(ts) AS last_ts"
        " FROM points GROUP BY trajectory_id ORDER BY trajectory_id"))


def get_trajectory_points(
    conn: sqlite3.Connection, trajectory_id: str
) -> list[sqlite3.Row]:
    """按采样时刻升序返回（乱序入库在查询时排序）。"""
    return list(conn.execute(
        "SELECT event_id, trajectory_id, x, y, ts FROM points WHERE trajectory_id=?"
        " ORDER BY ts, event_id", (trajectory_id,)))
