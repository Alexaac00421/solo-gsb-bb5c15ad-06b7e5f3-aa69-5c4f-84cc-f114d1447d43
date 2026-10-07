"""穿越分析：匀速插值、内孔拆分、多区域拆分、有效时段裁切、未覆盖时段、补录重查。"""
import pytest

ZONE = {
    "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
    "valid_from": "2026-01-01T00:00:00Z",
    "valid_to": "2027-01-01T00:00:00Z",
}


def _zone(code="Z1", holes=None, vf="2026-01-01T00:00:00Z", vt="2027-01-01T00:00:00Z", outer=None):
    return {
        "zone_code": code,
        "outer_ring": outer or ZONE["outer_ring"],
        "holes": holes or [],
        "valid_from": vf,
        "valid_to": vt,
    }


def _pt(event, track, x, y, day=1, hh=0, mm=0, ss=0):
    ts = f"2026-01-{day:02d}T{hh:02d}:{mm:02d}:{ss:02d}+00:00"
    return {"event_id": event, "track_no": track, "x": float(x), "y": float(y), "sampled_at": ts}


def _segments(client, track="T1"):
    return client.get(f"/api/tracks/{track}/intrusions").json()


def test_basic_enter_exit_uniform_speed(client):
    # 08:00:00 在 (-20,50)，08:00:20 在 (180,50)，10 m/s
    # 进入边界 x=0 于 t=2s，离开 x=100 于 t=12s -> [08:00:02, 08:00:12)
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    out = _segments(client)
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["zone_code"] == "Z1"
    assert s["enter_at"] == "2026-01-01T08:00:02.000000Z"
    assert s["exit_at"] == "2026-01-01T08:00:12.000000Z"
    assert s["enter_coord"] == [0.0, 50.0]
    assert s["exit_coord"] == [100.0, 50.0]


def test_points_out_of_order_are_connected_by_sample_time(client):
    client.put("/api/zones", json=_zone())
    # 先传“后面”的点，再传“前面”的点
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    out = _segments(client)
    assert [s["enter_at"] for s in out["segments"]] == ["2026-01-01T08:00:02.000000Z"]


def test_boundary_counts_half_open_interval(client):
    # 起点恰在边界 x=0 上，向内走：进入时刻 = 采样时刻（左闭）
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", 0, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 50, 50, hh=8, ss=10))
    out = _segments(client)
    assert len(out["segments"]) == 1
    assert out["segments"][0]["enter_at"] == "2026-01-01T08:00:00.000000Z"
    assert out["segments"][0]["exit_at"] == "2026-01-01T08:00:10.000000Z"


def test_tangent_single_point_is_not_a_fragment(client):
    # 沿 y=0（边界）外侧切线方向行进，只在一点贴边：线段从 (-20,-1) 到 (20,-1)
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -20, -1, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 20, -1, hh=8, ss=10))
    out = _segments(client)
    assert out["segments"] == []


def test_travel_along_boundary_is_forbidden(client):
    # 沿边界 y=0 行进（线段整体落在边界上）
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", 10, 0, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 90, 0, hh=8, ss=10))
    out = _segments(client)
    assert len(out["segments"]) == 1
    assert out["segments"][0]["enter_at"] == "2026-01-01T08:00:00.000000Z"
    assert out["segments"][0]["exit_at"] == "2026-01-01T08:00:10.000000Z"


def test_inner_hole_splits_one_segment_into_two(client):
    # 10 m/s 沿 y=50 横穿；内孔 [40,60]^2
    # 外区片段：进入 x=0 在 2s，孔界 x=40 在 6s，x=60 在 8s，出区 x=100 在 12s
    client.put("/api/zones", json=_zone(holes=[[[40, 40], [60, 40], [60, 60], [40, 60]]]))
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    out = _segments(client)
    assert len(out["segments"]) == 2
    first, second = out["segments"]
    assert (first["enter_at"], first["exit_at"]) == ("2026-01-01T08:00:02.000000Z",
                                                     "2026-01-01T08:00:06.000000Z")
    assert (second["enter_at"], second["exit_at"]) == ("2026-01-01T08:00:08.000000Z",
                                                       "2026-01-01T08:00:12.000000Z")
    # 两个片段完整覆盖“非孔”禁入时间
    assert first["exit_coord"][0] == 40.0
    assert second["enter_coord"][0] == 60.0


def test_multiple_overlapping_zones_split_and_tag(client):
    # Z1: [0,100]^2；Z2: [60,160] x [0,100]。沿 y=50 以 10m/s 行进。
    # x=60..100 同时位于两区内，两个区域各输出完整穿越片段。
    client.put("/api/zones", json=_zone("Z1"))
    client.put("/api/zones", json=_zone("Z2", outer=[[60, 0], [160, 0], [160, 100], [60, 100]]))
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    out = _segments(client)
    by_zone = {s["zone_code"]: s for s in out["segments"]}
    assert set(by_zone) == {"Z1", "Z2"}
    # Z1: x 0..100 -> 2..12s；Z2: x 60..160 -> 8..18s
    assert (by_zone["Z1"]["enter_at"], by_zone["Z1"]["exit_at"]) == (
        "2026-01-01T08:00:02.000000Z", "2026-01-01T08:00:12.000000Z")
    assert (by_zone["Z2"]["enter_at"], by_zone["Z2"]["exit_at"]) == (
        "2026-01-01T08:00:08.000000Z", "2026-01-01T08:00:18.000000Z")
    # 按时序排列：最先进入的排最前
    assert out["segments"][0]["enter_at"] == "2026-01-01T08:00:02.000000Z"


def test_validity_window_clips_segment(client):
    # 08:00:00 入区时刻为 08:00:02，08:00:20 离开；区域 08:00:05 生效，08:00:10 失效
    # 裁切后只有 [08:00:05, 08:00:10)
    client.put("/api/zones", json=_zone(vf="2026-01-01T08:00:05Z", vt="2026-01-01T08:00:10Z"))
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    out = _segments(client)
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["enter_at"] == "2026-01-01T08:00:05.000000Z"
    assert s["exit_at"] == "2026-01-01T08:00:10.000000Z"
    # 左闭右开：失效时刻不包含；裁切点坐标按匀速插值（30m, 80m）
    assert s["enter_coord"][0] == pytest.approx(30.0, abs=1e-6)
    assert s["exit_coord"][0] == pytest.approx(80.0, abs=1e-6)


def test_touch_zone_only_at_expiry_time_gives_nothing(client):
    # 几何穿越 [2,12)，有效时段在 t=2 整失效 -> 仅在边界时刻接触，无片段
    client.put("/api/zones", json=_zone(vf="2026-01-01T07:00:00Z", vt="2026-01-01T08:00:02Z"))
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 180, 50, hh=8, ss=20))
    out = _segments(client)
    assert out["segments"] == []


def test_gap_over_threshold_reported_without_presuming_path(client, monkeypatch):
    client.put("/api/zones", json=_zone())
    # 08:00:00 与 08:02:00 间隔 120s > 60s 阈值：输出未覆盖时段，不推测路径
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 120, 50, hh=8, mm=2))
    out = _segments(client)
    assert out["segments"] == []
    assert len(out["uncovered_gaps"]) == 1
    g = out["uncovered_gaps"][0]
    assert g["interval_seconds"] == 120.0
    assert g["from_at"] == "2026-01-01T08:00:00.000000Z"
    assert g["to_at"] == "2026-01-01T08:02:00.000000Z"


def test_gap_threshold_configurable(client, monkeypatch):
    monkeypatch.setenv("MAX_GAP_SECONDS", "120")
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 120, 50, hh=8, mm=2))
    out = _segments(client)
    # 恰好 120s，不超阈值 -> 正常构线（不登记 gap）
    assert out["uncovered_gaps"] == []
    assert len(out["segments"]) == 1


def test_consecutive_points_inside_zone_merge(client):
    # 三个点都在区内，跨采样点的两段应合并成一个完整片段
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", 10, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 50, 50, hh=8, ss=10))
    client.post("/api/points", json=_pt("c", "T1", 90, 50, hh=8, ss=20))
    out = _segments(client)
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["enter_at"] == "2026-01-01T08:00:00.000000Z"
    assert s["exit_at"] == "2026-01-01T08:00:20.000000Z"


def test_late_point_changes_adjacency_on_requery(client):
    # 先只有两个超阈值间隔的点 -> 未覆盖
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -20, 50, hh=8))
    client.post("/api/points", json=_pt("c", "T1", 180, 50, hh=8, mm=2))
    out = _segments(client)
    assert out["point_count"] == 2
    assert len(out["uncovered_gaps"]) == 1
    assert out["segments"] == []

    # 补录中间点 b（乱序到达）后重查：相邻线段变化，gap 消失，出现禁入片段
    client.post("/api/points", json=_pt("b", "T1", 50, 50, hh=8, mm=1))
    out = _segments(client)
    assert out["point_count"] == 3
    assert out["uncovered_gaps"] == []
    # 段 a-b：-20→50，60s 走 70m；入区 x=0 于 60*20/70 ≈ 17.142857s
    # 段 b-c：50→180，60s 走 130m；离区 x=100 于 60+60*50/130 ≈ 83.076923s
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["enter_at"] == "2026-01-01T08:00:17.142857Z"
    assert s["exit_at"] == "2026-01-01T08:01:23.076923Z"


def test_stationary_point_on_boundary_over_duration_is_segment(client):
    # 异时刻、同坐标、坐标在边界上：停留时段整体禁入
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", 0, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 0, 50, hh=8, ss=30))
    out = _segments(client)
    assert len(out["segments"]) == 1
    s = out["segments"][0]
    assert s["enter_at"] == "2026-01-01T08:00:00.000000Z"
    assert s["exit_at"] == "2026-01-01T08:00:30.000000Z"


def test_stationary_outside_is_nothing(client):
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -5, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", -5, 50, hh=8, ss=30))
    out = _segments(client)
    assert out["segments"] == []


def test_multiple_gaps_sorted(client):
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", -10, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", -10, 50, hh=8, mm=2))
    client.post("/api/points", json=_pt("c", "T1", -10, 50, hh=8, mm=5))
    out = _segments(client)
    assert [g["from_at"] for g in out["uncovered_gaps"]] == [
        "2026-01-01T08:00:00.000000Z",
        "2026-01-01T08:02:00.000000Z",
    ]


def test_tracks_are_independent(client):
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json=_pt("a", "T1", 10, 50, hh=8))
    client.post("/api/points", json=_pt("b", "T1", 90, 50, hh=8, ss=10))
    client.post("/api/points", json=_pt("a2", "T2", -50, -50, hh=8))
    client.post("/api/points", json=_pt("b2", "T2", -60, -60, hh=8, ss=10))
    assert len(_segments(client, "T1")["segments"]) == 1
    assert _segments(client, "T2")["segments"] == []


def test_timezone_offsets_normalised(client):
    # +08:00 的 16:00:00 == UTC 08:00:00
    client.put("/api/zones", json=_zone())
    client.post("/api/points", json={"event_id": "a", "track_no": "T1", "x": -20.0, "y": 50.0,
                                      "sampled_at": "2026-01-01T16:00:00+08:00"})
    client.post("/api/points", json={"event_id": "b", "track_no": "T1", "x": 180.0, "y": 50.0,
                                      "sampled_at": "2026-01-01T16:00:20+08:00"})
    out = _segments(client)
    assert out["segments"][0]["enter_at"] == "2026-01-01T08:00:02.000000Z"


def test_empty_track_query(client):
    out = _segments(client, "NOPE")
    assert out["point_count"] == 0
    assert out["segments"] == []
    assert out["uncovered_gaps"] == []
