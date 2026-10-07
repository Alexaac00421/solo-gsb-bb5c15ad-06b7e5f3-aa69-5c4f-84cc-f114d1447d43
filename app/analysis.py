"""Forbidden-zone crossing analysis on a single consistent data snapshot."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from shapely.geometry import Point

from .geometry import build_polygon, intersect_segment
from .repository import get_track_points, list_zones

Coord = tuple[float, float]


@dataclass(frozen=True)
class RawInterval:
    """Forbidden time interval belonging to one zone, in microseconds."""
    zone_code: str
    enter_us: int
    exit_us: int
    enter_coord: Coord
    exit_coord: Coord


@dataclass(frozen=True)
class RawGap:
    from_us: int
    to_us: int
    from_coord: Coord
    to_coord: Coord


def _parameter_at(p0: Coord, p1: Coord, q: Coord) -> float:
    """Position parameter t (0..1) of point q on segment p0->p1.

    Use the axis with the larger span for numerical stability and clamp to
    [0, 1] (intersection endpoints are on the segment in exact arithmetic).
    """
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if abs(dx) >= abs(dy):
        t = 0.0 if dx == 0.0 else (q[0] - p0[0]) / dx
    else:
        t = 0.0 if dy == 0.0 else (q[1] - p0[1]) / dy
    if t < 0.0:
        return 0.0
    if t > 1.0:
        return 1.0
    return t


def _time_at(t0_us: int, t1_us: int, t: float) -> int:
    us = t0_us + round(t * (t1_us - t0_us))
    return min(max(us, t0_us), t1_us)


def _coord_at(p0: Coord, p1: Coord, t: float) -> Coord:
    return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))


def _analyze_segment(
    p0: sqlite3.Row,
    p1: sqlite3.Row,
    zones: list,
    max_gap_us: int,
) -> tuple[list[RawInterval], RawGap | None]:
    a: Coord = (p0["x"], p0["y"])
    b: Coord = (p1["x"], p1["y"])
    t0, t1 = p0["sampled_at_us"], p1["sampled_at_us"]
    intervals: list[RawInterval] = []
    gap: RawGap | None = None

    if t1 - t0 > max_gap_us:
        # 超出阈值：两点之间路径不推测，登记为未覆盖时段，不产生任何片段。
        gap = RawGap(t0, t1, a, b)
        return intervals, gap

    span = t1 - t0
    stationary = a == b
    for zone in zones:
        vf, vt = zone["valid_from_us"], zone["valid_to_us"]
        # 区域时段同样为左闭右开；整条线段都在时段外则无交集。
        if stationary:
            # 静止点（异时刻同坐标）：覆盖则在整个重叠时段内处于禁入区。
            lo, hi = max(t0, vf), min(t1, vt)
            if lo < hi and zone["polygon"].covers(Point(a)):
                intervals.append(
                    RawInterval(
                        zone["zone_code"], lo, hi,
                        _coord_at(a, b, (lo - t0) / span),
                        _coord_at(a, b, (hi - t0) / span),
                    )
                )
            continue

        # 有效时段与线段时间范围在时间轴上的重叠（左闭右开）。
        lo, hi = max(t0, vf), min(t1, vt)
        if lo >= hi:
            continue
        for part in intersect_segment(zone["polygon"], a, b):
            ta = _parameter_at(a, b, part.a)
            tb = _parameter_at(a, b, part.b)
            enter_us = _time_at(t0, t1, ta)
            exit_us = _time_at(t0, t1, tb)
            # 按区域有效时段裁切 [enter, exit) ∩ [vf, vt)。
            enter_us = max(enter_us, vf)
            exit_us = min(exit_us, vt)
            if exit_us <= enter_us:
                # 单点接触（切点、仅在失效时刻接触边界等）不形成片段。
                continue
            enter_coord = _coord_at(a, b, (enter_us - t0) / span)
            exit_coord = _coord_at(a, b, (exit_us - t0) / span)
            intervals.append(
                RawInterval(zone["zone_code"], enter_us, exit_us, enter_coord, exit_coord)
            )

    return intervals, gap


def _merge_intervals(intervals: list[RawInterval]) -> list[RawInterval]:
    """Merge only exactly back-to-back intervals of the SAME zone.

    Splits across inner holes or different zones are preserved; here we only
    glue fragments separated by a sampling point when movement stayed inside
    the same zone.
    """
    by_zone: dict[str, list[RawInterval]] = {}
    for iv in intervals:
        by_zone.setdefault(iv.zone_code, []).append(iv)

    merged: list[RawInterval] = []
    for zone_code, items in by_zone.items():
        items.sort(key=lambda i: (i.enter_us, i.exit_us))
        cur: RawInterval | None = None
        for iv in items:
            if cur is None:
                cur = iv
            elif iv.enter_us == cur.exit_us:
                # 仅合并恰好在采样点处首尾相接的同一区域片段；
                # 内孔/区外造成的间隔即使时间相接也保持拆分。
                cur = RawInterval(zone_code, cur.enter_us, iv.exit_us,
                                  cur.enter_coord, iv.exit_coord)
            else:
                merged.append(cur)
                cur = iv
        if cur is not None:
            merged.append(cur)
    return merged


def analyze_track(conn: sqlite3.Connection, track_no: str, max_gap_seconds: float) -> dict:
    """Compute forbidden fragments and uncovered gaps for one track.

    All reads happen inside the caller/current transaction so they share one
    SQLite snapshot; adding points later and querying again yields new
    adjacency segments automatically.
    """
    zone_rows = list_zones(conn)
    zones = []
    for z in zone_rows:
        zones.append({**z, "polygon": build_polygon(z["outer_ring"], z["holes"])})

    points = get_track_points(conn, track_no)
    max_gap_us = round(max_gap_seconds * 1_000_000)

    raw_intervals: list[RawInterval] = []
    gaps: list[RawGap] = []
    for prev, cur in zip(points, points[1:]):
        if cur["sampled_at_us"] == prev["sampled_at_us"]:
            # 同轨迹同时刻的点在写入端已拒绝；若出现也拒绝参与构线。
            continue
        intervals, gap = _analyze_segment(prev, cur, zones, max_gap_us)
        raw_intervals.extend(intervals)
        if gap is not None:
            gaps.append(gap)

    segments = _merge_intervals(raw_intervals)
    # 时序排列；同时刻按区域编号保证输出确定。
    segments.sort(key=lambda i: (i.enter_us, i.zone_code))
    gaps.sort(key=lambda g: g.from_us)

    return {
        "track_no": track_no,
        "point_count": len(points),
        "segments": segments,
        "gaps": gaps,
    }
