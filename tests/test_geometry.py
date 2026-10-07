import pytest

from app.geometry import (
    GeometryError,
    forbidden_params,
    normalize_ring,
    point_in_polygon,
    validate_rings,
)


def poly(with_hole=False):
    outer = normalize_ring([(0, 0), (100, 0), (100, 100), (0, 100)])
    holes = []
    if with_hole:
        holes = [normalize_ring([(40, 40), (60, 40), (60, 60), (40, 60)])]
    validate_rings(outer, holes)
    return outer, holes


def test_point_in_polygon_with_hole_boundary_semantics():
    outer, holes = poly(True)
    assert point_in_polygon((50, 50), outer, holes) is False      # 孔内部
    assert point_in_polygon((40, 50), outer, holes) is True       # 孔边界计入
    assert point_in_polygon((50, 0), outer, holes) is True        # 外边界计入
    assert point_in_polygon((10, 10), outer, holes) is True
    assert point_in_polygon((110, 50), outer, holes) is False


def test_segment_crossing_hole_is_split():
    # 水平线 y=50 横穿：外 [20,80]，孔严格内部 (40,60) 扣除
    outer, holes = poly(True)
    ivs = forbidden_params((20, 50), (80, 50), outer, holes)
    assert len(ivs) == 2
    (t0, t1), (t2, t3) = ivs
    assert t0 == pytest.approx(0.0, abs=1e-9)
    assert t1 == pytest.approx(1 / 3, abs=1e-9)
    assert t2 == pytest.approx(2 / 3, abs=1e-9)
    assert t3 == pytest.approx(1.0, abs=1e-9)
    # 孔边界参数 t=1/3 与 t=2/3 成为切点，区间左闭右开互不相连


def test_single_point_touch_yields_no_interval():
    # 线段在区域外，仅在顶点 (100,100) 处点接触
    outer, holes = poly(False)
    ivs = forbidden_params((110, 110), (100, 100), outer, holes)
    assert ivs == []
    # 沿外边界的整段接触 => 属于禁入
    ivs2 = forbidden_params((0, 0), (100, 0), outer, holes)
    assert len(ivs2) == 1
    assert ivs2[0] == pytest.approx((0.0, 1.0))


def test_self_intersecting_polygon_rejected():
    # 非零面积但边交叉的蝴蝶结（两腰相交于 (50,50)）
    bowtie = [(0, 0), (100, 100), (100, 0), (0, 80)]
    outer = normalize_ring(bowtie)  # 面积非 0，过得了基本结构校验
    with pytest.raises(GeometryError):
        validate_rings(outer, [])

    # 对称蝴蝶结面积为 0，在规范化阶段即拒绝
    with pytest.raises(GeometryError):
        normalize_ring([(0, 0), (100, 100), (100, 0), (0, 100)])


def test_hole_touching_outer_rejected():
    outer, _ = poly(False)
    bad_hole = normalize_ring([(0, 50), (20, 50), (20, 60), (0, 60)])
    with pytest.raises(GeometryError):
        validate_rings(outer, [bad_hole])


def test_nested_holes_rejected():
    outer, _ = poly(False)
    h1 = normalize_ring([(20, 20), (80, 20), (80, 80), (20, 80)])
    h2 = normalize_ring([(40, 40), (60, 40), (60, 60), (40, 60)])
    with pytest.raises(GeometryError):
        validate_rings(outer, [h1, h2])


def test_degenerate_ring_rejected():
    with pytest.raises(GeometryError):
        normalize_ring([(0, 0), (5, 5), (10, 10)])
    with pytest.raises(GeometryError):
        normalize_ring([(0, 0), (1, 1)])
