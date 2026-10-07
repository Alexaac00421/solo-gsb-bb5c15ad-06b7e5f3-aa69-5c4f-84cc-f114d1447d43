"""Data-access functions for zones and points."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .geometry import build_polygon
from .timeutil import to_us


def now_us() -> int:
    return time.time_ns() // 1000


def _row_to_zone(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "zone_code": row["zone_code"],
        "outer_ring": json.loads(row["outer_ring"]),
        "holes": json.loads(row["holes"]),
        "valid_from_us": row["valid_from_us"],
        "valid_to_us": row["valid_to_us"],
    }


def get_zone(conn: sqlite3.Connection, zone_code: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM zones WHERE zone_code = ?", (zone_code,)).fetchone()
    return _row_to_zone(row) if row else None


def list_zones(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM zones ORDER BY zone_code").fetchall()
    return [_row_to_zone(r) for r in rows]


def upsert_zone(
    conn: sqlite3.Connection,
    *,
    zone_code: str,
    outer_ring: list,
    holes: list,
    valid_from_us: int,
    valid_to_us: int,
) -> bool:
    """Insert or replace a zone. Returns True if a new row was created.

    Caller MUST have validated the polygon beforehand; nothing is written on
    invalid input.
    """
    # Rebuild here as a last line of defence; raises on invalid input.
    build_polygon(outer_ring, holes)
    payload = (
        json.dumps(outer_ring, separators=(",", ":")),
        json.dumps(holes, separators=(",", ":")),
        valid_from_us,
        valid_to_us,
    )
    existing = conn.execute("SELECT 1 FROM zones WHERE zone_code = ?", (zone_code,)).fetchone()
    ts = now_us()
    if existing:
        conn.execute(
            """UPDATE zones
                  SET outer_ring = ?, holes = ?, valid_from_us = ?, valid_to_us = ?,
                      updated_at_us = ?
                WHERE zone_code = ?""",
            (*payload, ts, zone_code),
        )
        return False
    conn.execute(
        """INSERT INTO zones
               (zone_code, outer_ring, holes, valid_from_us, valid_to_us,
                created_at_us, updated_at_us)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (zone_code, *payload, ts, ts),
    )
    return True


class PointConflict(Exception):
    """409-class rejection; the whole batch is rolled back unchanged."""


def insert_points(conn: sqlite3.Connection, points: list) -> dict[str, int]:
    """Idempotent ingest.

    * 同事件号 + 完全相同内容 -> 去重，不重复写入；
    * 同事件号但内容不同 -> 拒绝；
    * 同轨迹 + 同采样时刻（异事件号；无论坐标异同）-> 拒绝。
    任何冲突都会回滚整批，不改动原有数据。
    """
    accepted = 0
    duplicates = 0
    seen_events: dict[str, tuple] = {}
    seen_track_ts: set[tuple[str, int]] = set()

    for p in points:
        ts_us = to_us(p.sampled_at)
        content = (p.track_no, p.x, p.y, ts_us)

        if p.event_id in seen_events:
            if seen_events[p.event_id] == content:
                duplicates += 1
                continue
            raise PointConflict(f"批次内事件号 {p.event_id} 重复且内容不一致")

        row = conn.execute(
            "SELECT track_no, x, y, sampled_at_us FROM points WHERE event_id = ?",
            (p.event_id,),
        ).fetchone()
        if row is not None:
            existing = (row["track_no"], row["x"], row["y"], row["sampled_at_us"])
            if existing == content:
                duplicates += 1
                continue
            raise PointConflict(
                f"事件号 {p.event_id} 已存在但内容不同（轨迹/坐标/采样时刻不一致），拒绝写入"
            )

        key = (p.track_no, ts_us)
        if key in seen_track_ts:
            raise PointConflict(
                f"轨迹 {p.track_no} 在采样时刻 {p.sampled_at.isoformat()} 已有另一个点，"
                "同一轨迹同一时刻不允许出现两个定位点"
            )
        clash = conn.execute(
            "SELECT event_id FROM points WHERE track_no = ? AND sampled_at_us = ?",
            key,
        ).fetchone()
        if clash is not None:
            raise PointConflict(
                f"轨迹 {p.track_no} 在采样时刻 {p.sampled_at.isoformat()} 已存在点"
                f"（事件号 {clash['event_id']}），同轨迹同时刻重复定位被拒绝"
            )

        conn.execute(
            """INSERT INTO points (event_id, track_no, x, y, sampled_at_us, created_at_us)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (p.event_id, p.track_no, p.x, p.y, ts_us, now_us()),
        )
        seen_events[p.event_id] = content
        seen_track_ts.add(key)
        accepted += 1

    return {"accepted": accepted, "duplicates": duplicates}


def get_track_points(conn: sqlite3.Connection, track_no: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT event_id, track_no, x, y, sampled_at_us
             FROM points WHERE track_no = ?
            ORDER BY sampled_at_us ASC, event_id ASC""",
        (track_no,),
    ).fetchall()
