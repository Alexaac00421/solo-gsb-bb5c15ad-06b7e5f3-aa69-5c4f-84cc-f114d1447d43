"""禁入区录入与校验测试。"""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_put_and_list_zone_with_hole(client):
    body = {
        "zone_code": "Z1",
        "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]],
        "valid_from": "2026-01-01T00:00:00+08:00",
        "valid_to": "2027-01-01T00:00:00+08:00",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["zone_code"] == "Z1"
    assert data["holes"][0][0] == [40.0, 40.0]
    # 输出为 UTC（+08:00 -> 前一天 16:00Z）
    assert data["valid_from"].endswith("Z")
    assert data["valid_from"].startswith("2025-12-31T16:00:00")

    r = client.get("/api/zones")
    assert r.status_code == 200
    assert [z["zone_code"] for z in r.json()["zones"]] == ["Z1"]


def test_zone_accepts_closed_ring(client):
    body = {
        "zone_code": "ZC",
        "outer_ring": [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 200, r.text


def test_self_intersecting_polygon_rejected_and_untouched(client):
    # 先录入一个合法区域
    ok = {
        "zone_code": "BAD",
        "outer_ring": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    assert client.put("/api/zones", json=ok).status_code == 200

    # 蝴蝶结（自交）多边形必须被拒绝
    bowtie = {
        "zone_code": "BAD",
        "outer_ring": [[0, 0], [10, 10], [0, 10], [10, 0]],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    r = client.put("/api/zones", json=bowtie)
    assert r.status_code == 422
    assert "无效多边形" in r.json()["detail"]

    # 原数据不变（仍然是合法矩形）
    z = client.get("/api/zones").json()["zones"][0]
    assert z["outer_ring"] == [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]


def test_degenerate_polygon_rejected(client):
    body = {
        "zone_code": "D",
        "outer_ring": [[0, 0], [10, 0], [20, 0]],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 422


def test_hole_outside_outer_rejected(client):
    body = {
        "zone_code": "H",
        "outer_ring": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "holes": [[[40, 40], [50, 40], [50, 50]]],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 422


def test_empty_validity_window_rejected(client):
    body = {
        "zone_code": "T",
        "outer_ring": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "valid_from": "2026-01-02T00:00:00Z",
        "valid_to": "2026-01-01T00:00:00Z",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 422


def test_naive_datetime_rejected(client):
    body = {
        "zone_code": "N",
        "outer_ring": [[0, 0], [10, 0], [10, 10], [0, 10]],
        "valid_from": "2026-01-01T00:00:00",
        "valid_to": "2027-01-01T00:00:00",
    }
    r = client.put("/api/zones", json=body)
    assert r.status_code == 422
