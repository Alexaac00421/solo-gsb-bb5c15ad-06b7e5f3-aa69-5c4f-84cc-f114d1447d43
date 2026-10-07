"""Polygon validation and line/polygon intersection in planar metre coords.

A forbidden zone is a valid simple (non-self-intersecting) outer ring with zero
or more inner rings (holes). Boundary is part of the forbidden area.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from shapely.geometry import LineString, Point, Polygon

# A footprint shorter than 1 nanometre is treated as a single point.
LENGTH_EPS = 1e-9
# Coordinates closer than this (metres) collapse onto each other.
COORD_EPS = 1e-9

Coord = tuple[float, float]


class GeometryError(ValueError):
    """Raised when a submitted ring/polygon is invalid."""


def _check_finite(coord: Sequence[float]) -> Coord:
    x, y = float(coord[0]), float(coord[1])
    if not (x == x and y == y) or x in (float("inf"), float("-inf")) or y in (float("inf"), float("-inf")):
        raise GeometryError(f"坐标包含非法数值: {coord!r}")
    return x, y


def _clean_ring(ring: Sequence[Sequence[float]]) -> list[Coord]:
    pts = [_check_finite(c) for c in ring]
    if len(pts) < 3:
        raise GeometryError("每个环至少需要 3 个点")
    # Accept a repeated closing point; shapely closes rings itself.
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) <= COORD_EPS and abs(pts[0][1] - pts[-1][1]) <= COORD_EPS:
        pts = pts[:-1]
    if len(pts) < 3:
        raise GeometryError("每个环至少需要 3 个互不重合的点")
    return pts


def build_polygon(outer: Sequence[Sequence[float]], holes: Sequence[Sequence[Sequence[float]]] | None = None) -> Polygon:
    """Validate and build a shapely Polygon; reject invalid/self-crossing input."""
    try:
        outer_pts = _clean_ring(outer)
        hole_pts = [_clean_ring(h) for h in (holes or [])]
        polygon = Polygon(outer_pts, hole_pts)
    except GeometryError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise GeometryError(f"多边形构造失败: {exc}") from exc

    # make_valid would silently repair bad input; instead reject it so the
    # caller's existing data is never overwritten by invalid geometry.
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0.0:
        reason = "自交或退化（面积为 0）"
        raise GeometryError(f"无效多边形: {reason}")
    if polygon.geom_type != "Polygon":
        raise GeometryError("外环与内孔必须构成单个多边形")
    return polygon


def polygon_coords(polygon: Polygon) -> tuple[list[Coord], list[list[Coord]]]:
    outer = list(polygon.exterior.coords)[:-1]
    holes = [list(ring.coords)[:-1] for ring in polygon.interiors]
    return [(x, y) for x, y in outer], [[(x, y) for x, y in h] for h in holes]


@dataclass(frozen=True)
class LinearPart:
    """One maximal sub-segment of the sampled segment lying inside a zone."""
    a: Coord
    b: Coord


def _line_parts(geom) -> Iterable[tuple[Coord, Coord]]:
    """Yield (start, end) coords of every linear piece of an intersection."""
    if geom.is_empty:
        return
    if geom.geom_type == "LineString":
        if len(geom.coords) >= 2:
            coords = list(geom.coords)
            yield coords[0], coords[-1]
        return
    if geom.geom_type == "MultiLineString":
        for line in geom.geoms:
            yield from _line_parts(line)
        return
    if geom.geom_type == "GeometryCollection":
        for g in geom.geoms:
            yield from _line_parts(g)
        return
    # Point / MultiPoint: touching only -> no fragment.


def intersect_segment(polygon: Polygon, p0: Coord, p1: Coord) -> list[LinearPart]:
    """Return maximal covered parts of segment p0->p1 (boundary included).

    A single-point contact (tangency, crossing at exactly one endpoint) is
    filtered out because the covered length is zero.
    """
    if abs(p0[0] - p1[0]) <= COORD_EPS and abs(p0[1] - p1[1]) <= COORD_EPS:
        pt = Point(p0)
        if polygon.covers(pt):
            return [LinearPart(p0, p0)]
        return []

    segment = LineString([p0, p1])
    covered = segment.intersection(polygon)
    parts: list[LinearPart] = []
    for a, b in _line_parts(covered):
        a = (float(a[0]), float(a[1]))
        b = (float(b[0]), float(b[1]))
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 > LENGTH_EPS:
            parts.append(LinearPart(a, b))
    return parts
