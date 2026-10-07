"""轨迹-禁入区复核核心算法（纯函数，无 IO）。

输出片段时间区间一律左闭右开 [start, end)：
- forbidden：线段在有效面内（边界计入；孔严格内部扣除；区域有效期裁切）；
- uncovered ：相邻采样点间隔超过阈值，路径未知，不做推测。

多个区域/内孔产生的切点合并为一组原子端点，逐段标注所在区域集合，
因此"穿越内孔"与"同时穿越多个区域"都会被完整拆分；相邻且区域集合
相同的片段再合并。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geometry import TIME_EPS, forbidden_params, normalize_ring, validate_rings


@dataclass(frozen=True)
class Zone:
    zone_id: int
    outer: list
    holes: list
    valid_from: float
    valid_until: float
    name: str | None = None

    @classmethod
    def from_row(cls, row) -> "Zone":
        import json
        return cls(
            zone_id=row["zone_id"],
            outer=json.loads(row["outer_ring"]),
            holes=json.loads(row["holes"]),
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            name=row["name"],
        )


@dataclass
class Segment:
    start: float
    end: float
    kind: str                      # "forbidden" | "uncovered"
    zone_ids: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "zone_ids": self.zone_ids,
        }


def build_zone(outer_raw, holes_raw, valid_from: float,
               valid_until: float) -> tuple[list, list]:
    """规范化并严格校验面；非法（含自交）抛 GeometryError，不触碰任何已存数据。"""
    outer = normalize_ring(outer_raw)
    holes = [normalize_ring(h) for h in holes_raw]
    validate_rings(outer, holes)
    return outer, holes


def _clip(t0: float, t1: float, lo: float, hi: float) -> tuple[float, float] | None:
    s, e = max(t0, lo), min(t1, hi)
    return (s, e) if e - s > TIME_EPS else None


def analyze(
    points: list,                 # rows: x, y, ts（已按 ts 升序）
    zones: list[Zone],
    gap_threshold: float,
    window: tuple[float | None, float | None] = (None, None),
) -> list[Segment]:
    # 原始带区域标注的禁入区间 (start, end, zone_id)
    raw: list[tuple[float, float, int]] = []
    uncovered: list[tuple[float, float]] = []

    for i in range(len(points) - 1):
        p1, p2 = points[i], points[i + 1]
        t1, t2 = float(p1["ts"]), float(p2["ts"])
        dt = t2 - t1
        if dt <= TIME_EPS:
            continue  # 零时长（UNIQUE 约束已防同刻重复，这里兜底不连边）
        if dt > gap_threshold + TIME_EPS:
            uncovered.append((t1, t2))  # 超阈值：不推测路径
            continue

        x1, y1, x2, y2 = float(p1["x"]), float(p1["y"]), float(p2["x"]), float(p2["y"])
        for z in zones:
            # 有效期左闭右开 [valid_from, valid_until)
            if z.valid_from >= t2 - TIME_EPS or z.valid_until <= t1 + TIME_EPS:
                continue
            for pa, pb in forbidden_params((x1, y1), (x2, y2), z.outer, z.holes):
                if pb - pa <= TIME_EPS:
                    continue  # 单点接触不形成片段
                s = t1 + pa * dt
                e = t1 + pb * dt
                clipped = _clip(s, e, z.valid_from, z.valid_until)
                if clipped:
                    raw.append((clipped[0], clipped[1], z.zone_id))

    win_lo, win_hi = window

    def in_window(t0: float, t1: float) -> tuple[float, float] | None:
        s = t0 if win_lo is None else max(t0, win_lo)
        e = t1 if win_hi is None else min(t1, win_hi)
        return (s, e) if e - s > TIME_EPS else None

    # ---- 多区域原子拆分 ----
    cuts: set[float] = set()
    for s, e, _ in raw:
        cuts.add(s)
        cuts.add(e)
    for s, e in uncovered:
        cuts.add(s)
        cuts.add(e)
    if win_lo is not None:
        cuts.add(win_lo)
    if win_hi is not None:
        cuts.add(win_hi)
    ts = sorted(c for c in cuts if (win_lo is None or c >= win_lo - TIME_EPS)
                and (win_hi is None or c <= win_hi + TIME_EPS))

    def covering(s: float, e: float, intervals) -> bool:
        m = (s + e) / 2
        return any(a - TIME_EPS <= m <= b + TIME_EPS for a, b in intervals)

    gap_intervals = [(a, b) for a, b in uncovered]
    raw_segments: list[Segment] = []
    for s, e in zip(ts, ts[1:]):
        if e - s <= TIME_EPS:
            continue
        if win_lo is not None and s < win_lo:
            s = win_lo
        if win_hi is not None and e > win_hi:
            e = win_hi
        if e - s <= TIME_EPS:
            continue
        if covering(s, e, gap_intervals):
            w = in_window(s, e)
            if w:
                raw_segments.append(Segment(w[0], w[1], "uncovered"))
            continue
        zids = sorted({zid for a, b, zid in raw
                       if a - TIME_EPS <= (s + e) / 2 <= b + TIME_EPS})
        if zids:
            w = in_window(s, e)
            if w:
                raw_segments.append(Segment(w[0], w[1], "forbidden", zids))

    # ---- 合并相邻同类同区域集合的禁入片段 ----
    # uncovered 不合并：相邻缺口的公共端点是一个真实采样点（位置已知），
    # 合并会把两个独立缺口误报成一个大缺口。
    raw_segments.sort(key=lambda seg: (seg.start, seg.end))
    merged: list[Segment] = []
    for seg in raw_segments:
        if (seg.kind == "forbidden" and merged
                and merged[-1].kind == seg.kind
                and merged[-1].zone_ids == seg.zone_ids
                and abs(seg.start - merged[-1].end) <= TIME_EPS):
            merged[-1].end = seg.end
        else:
            merged.append(seg)
    return merged
