"""二维平面（米坐标）多边形校验与线段裁切。

边界语义遵循 OGC SFS 简单面模型：
- 外边界（含）以内为区域；内孔边界与外边界一样属于面边界 => 计入禁入；
- 内孔的严格内部不属于区域；
- 因此"同一线段穿越内孔"会被拆分为 禁入(面内部) -> 非禁入(孔内部) -> 禁入。

所有计算基于有理数（double），比较采用相对 EPS 容差。
"""
from __future__ import annotations

from typing import Iterable

Point = tuple[float, float]

EPS = 1e-10
TIME_EPS = 1e-6


def _cross(ax: float, ay: float, bx: float, by: float) -> float:
    return ax * by - ay * bx


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def signed_area(ring: list[Point]) -> float:
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return s / 2.0


class GeometryError(ValueError):
    """无效或自交多边形。"""


def normalize_ring(ring: Iterable[Iterable[float]]) -> list[Point]:
    """校验环的基本结构并去掉与首点重复的闭合点；不做方向归一化。"""
    pts: list[Point] = []
    for v in ring:
        p = (float(v[0]), float(v[1]))
        if not pts or p != pts[-1]:
            pts.append(p)
    if len(pts) < 3:
        raise GeometryError("环至少需要 3 个不同顶点（自闭合点可省略）")
    if abs(signed_area(pts + [pts[0]])) < EPS:
        raise GeometryError("环面积为 0")
    return pts


def on_segment(p: Point, a: Point, b: Point, eps: float = EPS) -> bool:
    """p 是否位于闭线段 ab 上。"""
    if abs(_cross(p[0] - a[0], p[1] - a[1], b[0] - a[0], b[1] - a[1])) > eps:
        return False
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]) + eps
    )


def _segment_params(p: Point, q: Point, a: Point, b: Point):
    """返回线段 pq 与 ab 的交点在 pq 上的参数 t 列表（每个 0..1）。

    不假设端点不在线上；调用方按需去重。
    """
    r = _sub(q, p)
    s = _sub(b, a)
    rxs = _cross(r[0], r[1], s[0], s[1])
    apx, apy = a[0] - p[0], a[1] - p[1]   # a - p
    scale = max(
        1.0,
        abs(r[0]), abs(r[1]), abs(s[0]), abs(s[1]),
        abs(apx), abs(apy),
    )
    tol = EPS * scale * scale

    if abs(rxs) > EPS * scale * scale:
        t = _cross(apx, apy, s[0], s[1]) / rxs
        u = _cross(apx, apy, r[0], r[1]) / rxs
        if -EPS <= t <= 1.0 + EPS and -EPS <= u <= 1.0 + EPS:
            return [min(1.0, max(0.0, t))]
        return []

    # 平行：共线时求重叠区间
    if abs(_cross(apx, apy, s[0], s[1])) > tol:
        return []
    if not (s[0] or s[1]):
        # ab 退化为点
        return [0.0] if on_segment(a, p, q) else []
    t_a = ((a[0] - p[0]) * s[0] + (a[1] - p[1]) * s[1]) / (s[0] ** 2 + s[1] ** 2)
    t_b = ((b[0] - p[0]) * s[0] + (b[1] - p[1]) * s[1]) / (s[0] ** 2 + s[1] ** 2)
    lo, hi = sorted((t_a, t_b))
    lo = max(0.0, lo)
    hi = min(1.0, hi)
    if lo > hi + EPS:
        return []
    return [lo, hi]


def edge_relation(p: Point, q: Point, a: Point, b: Point) -> str:
    """两条线段的关系：'cross'（存在非退化交叠）/ 'touch'（仅一点）/ 'none'。"""
    ts = _segment_params(p, q, a, b)
    if not ts:
        return "none"
    if len(ts) == 1:
        return "cross"  # 单参数交点（含 T 接、端点相接），由调用方结合端点判断
    lo, hi = ts
    if hi - lo <= EPS:
        return "touch"
    return "cross"


def _edges(ring: list[Point]):
    n = len(ring)
    for i in range(n):
        yield ring[i], ring[(i + 1) % n]


def _share_endpoint(p: Point, q: Point, a: Point, b: Point) -> bool:
    for x in (p, q):
        for y in (a, b):
            if abs(x[0] - y[0]) <= EPS and abs(x[1] - y[1]) <= EPS:
                return True
    return False


def validate_rings(outer: list[Point], holes: list[list[Point]]) -> None:
    """校验简单多边形：外环自洽、孔洞自洽且严格位于外环内、孔洞互不相交/包含。

    任何不满足均抛 GeometryError；不抛异常即合法。
    """
    # 1) 各环不自交（允许相邻边共享端点，禁止非相邻边任何接触）
    for label, ring in [("外环", outer)] + [("内孔", h) for h in holes]:
        edges = list(_edges(ring))
        n = len(edges)
        for i in range(n):
            p, q = edges[i]
            for j in range(i + 1, n):
                a, b = edges[j]
                rel = edge_relation(p, q, a, b)
                if rel == "none":
                    continue
                adjacent = (j == i + 1) or (i == 0 and j == n - 1)
                if adjacent and _share_endpoint(p, q, a, b):
                    # 相邻边共享端点（collinear touch at the shared vertex 会同时表现为 overlap，拒绝）
                    shared = [x for x in (p, q) for y in (a, b)
                              if abs(x[0] - y[0]) <= EPS and abs(x[1] - y[1]) <= EPS]
                    other_ts = _segment_params(p, q, a, b)
                    # 正常相邻边：仅在公共顶点相交，参数集合恰为 {0} 或 {1}
                    if len(other_ts) == 1:
                        continue
                    raise GeometryError(f"{label}相邻边在公共顶点之外接触（可能共线回折）")
                raise GeometryError(f"{label}自交：非相邻边相交或接触")

    # 2) 每个孔洞严格位于外环内部：所有顶点 strict inside，且边与外环无交叉
    def strict_inside(ring: list[Point], container: list[Point]) -> bool:
        return all(point_in_ring(v, container, boundary=False) for v in ring)

    for h in holes:
        if not strict_inside(h, outer):
            raise GeometryError("内孔必须整体严格位于外环内部（孔边界不得触碰外边界）")
        for p, q in _edges(h):
            for a, b in _edges(outer):
                if edge_relation(p, q, a, b) != "none":
                    raise GeometryError("内孔边与外边界相交")

    # 3) 孔洞两两不相交、不接触、不嵌套
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            h1, h2 = holes[i], holes[j]
            for p, q in _edges(h1):
                for a, b in _edges(h2):
                    if edge_relation(p, q, a, b) != "none":
                        raise GeometryError("内孔之间相交或接触")
            v = h2[0]
            if point_in_ring(v, h1, boundary=False):
                raise GeometryError("内孔不得相互嵌套")


def point_in_ring(p: Point, ring: list[Point], boundary: bool = True) -> bool:
    """射线法；boundary=True 时环上点返回 True。"""
    n = len(ring)
    inside = False
    x, y = p
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if on_segment(p, ring[j], ring[i]):
            return boundary
        if ((yi > y) != (yj > y)):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
            if xint > x + EPS:  # 向左的水平射线：交点在点右侧
                inside = not inside
        j = i
    return inside


def _on_ring(p: Point, ring: list[Point]) -> bool:
    return any(on_segment(p, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))


def point_in_polygon(
    p: Point, outer: list[Point], holes: list[list[Point]] | None = None,
    boundary: bool = True,
) -> bool:
    """点是否在带孔面内。孔边界与外边界一致：boundary=True 时计 True。"""
    if not point_in_ring(p, outer, boundary):
        return False
    for h in holes or []:
        if _on_ring(p, h):
            return boundary  # 孔边界属于面边界
        if point_in_ring(p, h, boundary=False):
            return False     # 孔的严格内部
    return True


def forbidden_params(
    p: Point, q: Point, outer: list[Point], holes: list[list[Point]]
) -> list[tuple[float, float]]:
    """线段 p->q 在面内的参数区间集合（边界计入，孔严格内部扣除）。

    返回 [t0, t1) 形式的不交区间列表，t 为沿 p->q 的线性参数，0<=t<=1。
    单点（t0==t1）不产生区间。
    """
    cuts: set[float] = {0.0, 1.0}

    def collect(ring: list[Point]) -> None:
        for a, b in _edges(ring):
            for t in _segment_params(p, q, a, b):
                if -EPS <= t <= 1.0 + EPS:
                    cuts.add(min(1.0, max(0.0, t)))

    collect(outer)
    for h in holes:
        collect(h)

    ts = sorted(cuts)
    # 去除计算噪声产生的重复切点
    deduped: list[float] = []
    for t in ts:
        if not deduped or t - deduped[-1] > EPS:
            deduped.append(t)
    ts = deduped

    def classify(t0: float, t1: float) -> bool:
        """开区间 (t0,t1) 的归属：区间内部成分恒定。在区间内多个比例处
        采样，只要有点在面内（含边界）即为禁入；这样线段与边共线、
        中点恰在边界上时也能正确归属（边界计入，邻接面内部）。"""
        span = t1 - t0
        for frac in (0.5, 0.25, 0.75, 0.01, 0.99):
            t = t0 + span * frac
            x = p[0] + (q[0] - p[0]) * t
            y = p[1] + (q[1] - p[1]) * t
            if point_in_polygon((x, y), outer, holes, boundary=True):
                return True
        return False

    intervals: list[tuple[float, float]] = []
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 <= EPS:
            continue
        if classify(t0, t1):
            intervals.append((t0, t1))

    # 合并相邻区间（端点重合，分类相同）
    merged: list[tuple[float, float]] = []
    for t0, t1 in intervals:
        if merged and t0 - merged[-1][1] <= EPS:
            merged[-1] = (merged[-1][0], t1)
        else:
            merged.append((t0, t1))
    return merged
