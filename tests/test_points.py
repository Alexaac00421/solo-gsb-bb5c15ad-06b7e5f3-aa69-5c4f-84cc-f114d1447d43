"""定位点录入：幂等、冲突拒绝、乱序。"""


def _point(event="e1", track="T1", x=0.0, y=0.0, t="2026-01-01T00:00:00+08:00"):
    return {"event_id": event, "track_no": track, "x": x, "y": y, "sampled_at": t}


def test_single_and_batch_ingest(client):
    r = client.post("/api/points", json=_point())
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 1

    batch = {
        "points": [
            _point("e2", x=10, t="2026-01-01T00:00:10+08:00"),
            _point("e3", x=20, t="2026-01-01T00:00:20+08:00"),
        ]
    }
    r = client.post("/api/points", json=batch)
    assert r.status_code == 201, r.text
    assert r.json()["accepted"] == 2


def test_duplicate_same_content_is_idempotent(client):
    p = _point()
    assert client.post("/api/points", json=p).json()["accepted"] == 1
    # 原样重传：不重复写入
    r = client.post("/api/points", json=p)
    assert r.status_code == 201
    body = r.json()
    assert body["accepted"] == 0
    assert body["duplicates"] == 1
    r = client.get("/api/tracks/T1/intrusions")
    assert r.json()["point_count"] == 1


def test_same_event_different_content_rejected_without_change(client):
    client.post("/api/points", json=_point(x=0))
    # 同事件号、坐标不同 -> 拒绝
    r = client.post("/api/points", json=_point(x=5))
    assert r.status_code == 409
    # 同事件号、轨迹不同 -> 拒绝
    r = client.post("/api/points", json=_point(track="T9"))
    assert r.status_code == 409
    # 原有数据仍只有 1 个点，且坐标为 0
    intr = client.get("/api/tracks/T1/intrusions").json()
    assert intr["point_count"] == 1
    assert client.get("/api/tracks/T9/intrusions").json()["point_count"] == 0


def test_same_track_same_timestamp_rejected(client):
    client.post("/api/points", json=_point("e1", x=0, t="2026-01-01T00:00:00+08:00"))
    # 异坐标
    r = client.post(
        "/api/points",
        json=_point("e2", x=1, t="2026-01-01T00:00:00+08:00"),
    )
    assert r.status_code == 409
    # 同坐标也拒绝（时间线上不得有两个零间隔点）
    r = client.post(
        "/api/points",
        json=_point("e3", x=0, t="2026-01-01T00:00:00+08:00"),
    )
    assert r.status_code == 409
    intr = client.get("/api/tracks/T1/intrusions").json()
    assert intr["point_count"] == 1


def test_batch_rollback_on_conflict(client):
    batch = {
        "points": [
            _point("e1", x=0, t="2026-01-01T00:00:00+08:00"),
            _point("e2", x=10, t="2026-01-01T00:00:10+08:00"),
        ]
    }
    client.post("/api/points", json=batch)

    bad_batch = {
        "points": [
            _point("e3", x=20, t="2026-01-01T00:00:20+08:00"),
            _point("e1", x=999, t="2026-01-01T00:00:00+08:00"),  # 冲突
        ]
    }
    r = client.post("/api/points", json=bad_batch)
    assert r.status_code == 409
    # e3 必须随整批回滚
    intr = client.get("/api/tracks/T1/intrusions").json()
    assert intr["point_count"] == 2


def test_invalid_payload_rejected(client):
    r = client.post("/api/points", json={"event_id": "", "track_no": "T1",
                                          "x": 0, "y": 0,
                                          "sampled_at": "2026-01-01T00:00:00+08:00"})
    assert r.status_code == 422
    r = client.post("/api/points", json={"points": []})
    assert r.status_code == 422
    # Python JSON 默认会编码 NaN；服务端 pydantic 必须拒绝非有限坐标
    r = client.post(
        "/api/points",
        content='{"event_id":"e1","track_no":"T1","x":NaN,"y":0,'
                '"sampled_at":"2026-01-01T00:00:00+08:00"}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_duplicate_inside_batch(client):
    batch = {
        "points": [
            _point("e1", x=0, t="2026-01-01T00:00:00+08:00"),
            _point("e1", x=0, t="2026-01-01T00:00:00+08:00"),
        ]
    }
    r = client.post("/api/points", json=batch)
    assert r.status_code == 201
    assert r.json()["accepted"] == 1
    assert r.json()["duplicates"] == 1
