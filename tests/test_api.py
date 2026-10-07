"""端到端工作流：区域录入（含内孔/有效期）、乱序点、幂等/冲突、
穿越拆分、超阈值未覆盖、多区域、补录后重查、无效多边形拒绝。

注意：用例共享同一数据库，定义顺序即执行顺序。Z3（多区域重叠）
覆盖范围较大，因此其创建放在最后，避免影响切线/边界用例。
"""

import pytest

from tests.conftest import client  # noqa: F401

TZ = "+08:00"


def iso(h, m, s=0):
    return f"2026-10-07T{h:02d}:{m:02d}:{s:02d}{TZ}"


def post_points(client, pts):
    return client.post("/points", json={"points": pts})


def kinds_sorted(segs):
    times = [s["start"] for s in segs]
    return times == sorted(times)


@pytest.fixture(scope="module")
def zones(client):
    r = client.post("/zones", json={
        "name": "Z1-hole",
        "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]],
        "valid_from": iso(0, 0), "valid_until": iso(23, 59, 59),
    })
    assert r.status_code == 201, r.text
    z1 = r.json()["zone_id"]

    r = client.post("/zones", json={
        "name": "Z2-late",
        "outer_ring": [[100, 0], [130, 0], [130, 100], [100, 100]],
        "holes": [],
        "valid_from": iso(9, 0), "valid_until": iso(23, 0),
    })
    assert r.status_code == 201, r.text
    z2 = r.json()["zone_id"]
    return z1, z2


def test_a_invalid_polygon_rejected_and_data_untouched(client, zones):
    before = client.get("/zones").json()
    r = client.post("/zones", json={
        "outer_ring": [[0, 0], [100, 100], [100, 0], [0, 80]],  # 非零面积蝴蝶结自交
        "holes": [],
        "valid_from": iso(0, 0), "valid_until": iso(23, 0),
    })
    assert r.status_code == 422
    assert "自交" in r.json()["detail"]
    assert client.get("/zones").json() == before  # 拒绝后原数据不变

    # 孔触碰外边界同样拒绝
    r = client.post("/zones", json={
        "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "holes": [[[0, 50], [20, 50], [20, 60], [0, 60]]],
        "valid_from": iso(0, 0), "valid_until": iso(23, 0),
    })
    assert r.status_code == 422

    # 退化环（共线）拒绝
    r = client.post("/zones", json={
        "outer_ring": [[0, 0], [5, 5], [10, 10]],
        "holes": [],
        "valid_from": iso(0, 0), "valid_until": iso(23, 0),
    })
    assert r.status_code == 422
    assert client.get("/zones").json() == before


def test_b_tangent_and_boundary(client, zones):
    # 仅在顶点 (100,100) 处点接触 Z1：不形成片段
    T = "TRAJ-TANGENT"
    pts = [
        {"event_id": "T1", "trajectory_id": T, "x": 110, "y": 110, "ts": iso(6, 0)},
        {"event_id": "T2", "trajectory_id": T, "x": 100, "y": 100, "ts": iso(6, 1)},
        {"event_id": "T3", "trajectory_id": T, "x": 110, "y": 90, "ts": iso(6, 2)},
    ]
    assert post_points(client, pts).status_code == 201
    segs = client.get(f"/trajectories/{T}/segments").json()
    assert all(s["kind"] != "forbidden" for s in segs)

    # 沿外边界行进：整段计入禁入
    T2 = "TRAJ-EDGE"
    pts = [
        {"event_id": "B1", "trajectory_id": T2, "x": 0, "y": 0, "ts": iso(6, 0)},
        {"event_id": "B2", "trajectory_id": T2, "x": 100, "y": 0, "ts": iso(6, 2)},
    ]
    assert post_points(client, pts).status_code == 201
    segs = client.get(f"/trajectories/{T2}/segments").json()
    assert len(segs) == 1 and segs[0]["zone_ids"] == [zones[0]]


def test_c_trajectory_hole_gap_backfill(client, zones):
    z1, z2 = zones
    T = "TRAJ-1"

    batch1 = [
        # 乱序到达
        {"event_id": "E2", "trajectory_id": T, "x": 110, "y": 50, "ts": iso(8, 2)},
        {"event_id": "E1", "trajectory_id": T, "x": -10, "y": 50, "ts": iso(8, 0)},
        {"event_id": "E3", "trajectory_id": T, "x": 120, "y": 50, "ts": iso(8, 30)},
    ]
    r = post_points(client, batch1)
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 3

    # 查询时按采样时刻排序
    pts = client.get(f"/trajectories/{T}/points").json()
    assert [p["event_id"] for p in pts] == ["E1", "E2", "E3"]

    # 同事件号同内容重传：幂等，不新增
    r = post_points(client, [batch1[0]])
    assert r.status_code == 201 and r.json()["accepted"] == 0

    # 同事件号异内容：409
    r = post_points(client, [{
        "event_id": "E1", "trajectory_id": T, "x": 999, "y": 999, "ts": iso(8, 0)}])
    assert r.status_code == 409

    # 同轨迹同时刻异坐标：409
    r = post_points(client, [{
        "event_id": "E9", "trajectory_id": T, "x": 1, "y": 2, "ts": iso(8, 0)}])
    assert r.status_code == 409

    # 整批原子：含一条冲突则全批不落库
    n_before = len(client.get(f"/trajectories/{T}/points").json())
    r = post_points(client, [
        {"event_id": "E8", "trajectory_id": T, "x": 0, "y": 0, "ts": iso(7, 0)},
        {"event_id": "E1", "trajectory_id": T, "x": 0, "y": 0, "ts": iso(8, 0)},
    ])
    assert r.status_code == 409
    assert len(client.get(f"/trajectories/{T}/points").json()) == n_before

    segs = client.get(f"/trajectories/{T}/segments").json()
    assert kinds_sorted(segs)

    fb = [s for s in segs if s["zone_ids"] == [z1]]
    # 边 E1->E2（120s 内 x:-10->110，1m/s）：x=0 入 @10s，孔 40..60
    # 对应 @50s/@70s，x=100 出 @110s -> 孔把线段完整拆为两段
    assert len(fb) == 2
    assert fb[0]["start"].endswith("00:00:10Z")
    assert fb[0]["end"].endswith("00:00:50Z")
    assert fb[1]["start"].endswith("00:01:10Z")
    assert fb[1]["end"].endswith("00:01:50Z")

    gap = [s for s in segs if s["kind"] == "uncovered"]
    # E2->E3 间隔 1680s > 300：整段未覆盖，不推测是否穿过尚未生效的 Z2
    assert len(gap) == 1
    assert gap[0]["start"].endswith("00:02:00Z")
    assert gap[0]["end"].endswith("00:30:00Z")
    assert all(s["zone_ids"] != [z2] for s in segs)

    # 补录 09:00 之后的点：E3->E4 仍超阈值，但 E4->E5 构成相邻线段，
    # Z2 已生效 => 重查反映新线段与新禁入片段
    r = post_points(client, [
        {"event_id": "E4", "trajectory_id": T, "x": 120, "y": 50, "ts": iso(9, 0)},
        {"event_id": "E5", "trajectory_id": T, "x": 125, "y": 50, "ts": iso(9, 2)},
    ])
    assert r.status_code == 201
    segs2 = client.get(f"/trajectories/{T}/segments").json()
    z2_segs = [s for s in segs2 if s["zone_ids"] == [z2]]
    assert len(z2_segs) == 1
    assert z2_segs[0]["start"].endswith("01:00:00Z")
    assert z2_segs[0]["end"].endswith("01:02:00Z")
    assert len([s for s in segs2 if s["kind"] == "uncovered"]) == 2

    # 同一请求的点与区域来自同一快照（一致性由只读事务保证；这里仅校验接口稳定）
    again = client.get(f"/trajectories/{T}/segments").json()
    assert again == segs2

    # 查询窗裁切（左闭右开）
    win = client.get(f"/trajectories/{T}/segments",
                     params={"start": iso(9, 1), "end": iso(9, 3)}).json()
    assert all(s["start"] >= "2026-10-07T01:01:00Z" for s in win)
    assert all(s["end"] <= "2026-10-07T01:03:00Z" for s in win)
    assert any(s["zone_ids"] == [z2] for s in win)


def test_d_multi_zone_overlap_split(client, zones):
    z1, z2 = zones
    r = client.post("/zones", json={
        "name": "Z3-overlap",
        "outer_ring": [[80, -10], [110, -10], [110, 110], [80, 110]],
        "holes": [],
        "valid_from": iso(0, 0), "valid_until": iso(23, 59),
    })
    assert r.status_code == 201, r.text
    z3 = r.json()["zone_id"]

    T = "TRAJ-MULTI"
    pts = [
        {"event_id": "M1", "trajectory_id": T, "x": 60, "y": 50, "ts": iso(10, 0)},
        {"event_id": "M2", "trajectory_id": T, "x": 120, "y": 50, "ts": iso(10, 2)},
    ]
    assert post_points(client, pts).status_code == 201

    segs = client.get(f"/trajectories/{T}/segments").json()
    assert kinds_sorted(segs)
    # x 60->120（0.5m/s）：切点 x=80(Z3入)@40s, 100(Z1出/Z2入)@80s,
    # 110(Z3出)@100s；区域集合依次为 {Z1},{Z1,Z3},{Z2,Z3},{Z2}
    sets = [tuple(s["zone_ids"]) for s in segs]
    assert sets == [(z1,), (z1, z3), (z2, z3), (z2,)]
    # 输入为 +08:00，响应统一为 UTC（减 8 小时）：10:00+08 = 02:00Z
    bounds = [(s["start"], s["end"]) for s in segs]
    assert bounds == [
        ("2026-10-07T02:00:00Z", "2026-10-07T02:00:40Z"),
        ("2026-10-07T02:00:40Z", "2026-10-07T02:01:20Z"),
        ("2026-10-07T02:01:20Z", "2026-10-07T02:01:40Z"),
        ("2026-10-07T02:01:40Z", "2026-10-07T02:02:00Z"),
    ]


def test_e_naive_timestamp_rejected(client):
    r = post_points(client, [{
        "event_id": "NZ1", "trajectory_id": "X", "x": 0, "y": 0,
        "ts": "2026-10-07T08:00:00"}])
    assert r.status_code == 422


def test_f_not_found_and_trajectories_list(client):
    assert client.get("/trajectories/NOPE/segments").status_code == 404
    assert client.get("/zones/9999").status_code == 404
    tj = {t["trajectory_id"] for t in client.get("/trajectories").json()}
    assert {"TRAJ-1", "TRAJ-MULTI", "TRAJ-TANGENT", "TRAJ-EDGE"} <= tj


def test_g_concurrent_requests_use_independent_connections(client):
    """并发只读查询与写入不产生 'transaction within transaction' 错误。"""
    import concurrent.futures

    def workload(i: int):
        r1 = client.get("/trajectories/TRAJ-1/segments")
        r2 = client.post("/points", json={"points": [{
            "event_id": f"CC-{i}", "trajectory_id": "TRAJ-CONC",
            "x": float(i), "y": float(i), "ts": f"2026-10-07T11:{i:02d}:00+08:00"}]})
        return r1.status_code, r2.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(workload, range(16)))
    assert all(codes == (200, 201) for codes in results)
    # 乱序并发录入后查询仍按采样时刻排序
    ids = [p["event_id"] for p in
           client.get("/trajectories/TRAJ-CONC/points").json()]
    assert ids == sorted(ids, key=lambda e: int(e.split("-")[1]))
