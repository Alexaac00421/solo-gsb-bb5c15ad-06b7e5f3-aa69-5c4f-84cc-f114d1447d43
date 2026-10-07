"""analysis 层：内孔边界、有效期裁切、匀速插值、未覆盖缺口。"""
from app.analysis import Zone, analyze


def row(eid, x, y, ts):
    return {"event_id": eid, "x": x, "y": y, "ts": float(ts)}


def make_zone(zid=1, vf=0.0, vu=10000.0):
    return Zone(
        zone_id=zid,
        outer=[[0, 0], [100, 0], [100, 100], [0, 100]],
        holes=[[[40, 40], [60, 40], [60, 60], [40, 60]]],
        valid_from=vf, valid_until=vu,
    )


def test_constant_speed_interpolation_and_left_closed_right_open():
    z = make_zone()
    pts = [row("a", -10, 50, 0), row("b", 110, 50, 120)]
    segs = analyze(pts, [z], 300)
    fb = [s for s in segs if s.kind == "forbidden"]
    assert [(s.start, s.end) for s in fb] == [(10.0, 50.0), (70.0, 110.0)]
    # 左闭右开：前一段 end==后一段相关切点，无重叠
    assert fb[0].zone_ids == fb[1].zone_ids == [1]


def test_travel_along_hole_boundary_is_forbidden():
    z = make_zone()
    # 沿孔的下边 y=40，x 0..100：外边界内 + 孔边（边界计入）=> 全程禁入
    pts = [row("a", 0, 40, 0), row("b", 100, 40, 100)]
    segs = analyze(pts, [z], 300)
    fb = [s for s in segs if s.kind == "forbidden"]
    assert len(fb) == 1
    assert (fb[0].start, fb[0].end) == (0.0, 100.0)


def test_single_vertex_contact_no_segment():
    z = make_zone()
    pts = [row("a", 110, 110, 0), row("b", 100, 100, 60), row("c", 110, 90, 120)]
    segs = analyze(pts, [z], 300)
    assert segs == []


def test_validity_window_clips_segment():
    # 物理禁入区间为 [10,50) 与 [70,110)（中间为孔）；区域仅在 [40,80)
    # 有效 => 裁切为 [40,50) 与 [70,80)，失效时刻 80 为右开
    z = make_zone(vf=40.0, vu=80.0)
    pts = [row("a", -10, 50, 0), row("b", 110, 50, 120)]
    segs = analyze(pts, [z], 300)
    fb = [(s.start, s.end) for s in segs if s.kind == "forbidden"]
    assert fb == [(40.0, 50.0), (70.0, 80.0)]


def test_gap_threshold_outputs_uncovered_and_no_guess():
    z = make_zone()
    pts = [row("a", 0, 50, 0), row("b", 0, 50, 400), row("c", 0, 50, 500)]
    segs = analyze(pts, [z], 300)
    # 0->400 超阈值：uncovered；两个端点虽都在区域内，也不输出禁入
    unc = [s for s in segs if s.kind == "uncovered"]
    assert [(s.start, s.end) for s in unc] == [(0.0, 400.0)]
    # 400->500 在阈值内且静止于区域内：整段禁入（无切点时按中点分类）
    fb = [s for s in segs if s.kind == "forbidden"]
    assert [(s.start, s.end) for s in fb] == [(400.0, 500.0)]


def test_two_adjacent_gaps_are_separate():
    z = make_zone()
    pts = [row("a", 0, 0, 0), row("b", 0, 0, 400), row("c", 0, 0, 900)]
    segs = analyze(pts, [z], 300)
    unc = [(s.start, s.end) for s in segs if s.kind == "uncovered"]
    assert unc == [(0.0, 400.0), (400.0, 900.0)]


def test_query_window():
    z = make_zone()
    pts = [row("a", -10, 50, 0), row("b", 110, 50, 120)]
    segs = analyze(pts, [z], 300, window=(60.0, 75.0))
    # 物理区间 [10,50) 与 [70,110)；窗内仅剩 [70,75)
    assert [(s.start, s.end, s.kind) for s in segs] == [(70.0, 75.0, "forbidden")]


def test_degenerate_segment_stationary_inside():
    z = make_zone()
    # 静止点位于面内（非孔内）：整段禁入；位于孔内则不输出
    pts = [row("a", 10, 10, 0), row("b", 10, 10, 60)]
    segs = analyze(pts, [z], 300)
    assert [(s.start, s.end) for s in segs] == [(0.0, 60.0)]

    pts2 = [row("a", 50, 50, 0), row("b", 50, 50, 60)]
    assert analyze(pts2, [z], 300) == []
