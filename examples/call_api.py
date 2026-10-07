"""调用示例：录入区域/轨迹点并查询禁入穿越片段。

运行（需先启动服务，见 README）：
    python examples/call_api.py
也可指定地址：
    BASE_URL=http://127.0.0.1:8080 python examples/call_api.py

脚本只依赖标准库。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8080")


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def main() -> None:
    # 1) 录入禁入区：100m x 100m 矩形，中间带 20m x 20m 内孔，当日 08:00-18:00 生效
    zone = {
        "zone_code": "ZONE-001",
        "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]],
        "valid_from": "2026-10-07T08:00:00+08:00",
        "valid_to": "2026-10-07T18:00:00+08:00",
    }
    print("PUT  /api/zones ->", call("PUT", "/api/zones", zone)[0])

    # 2) 乱序批量录入定位点（轨迹 L-9，10 m/s 横穿区域；最后一个点间隔过大）
    points = {
        "points": [
            {"event_id": "evt-3", "track_no": "L-9", "x": 180.0, "y": 50.0,
             "sampled_at": "2026-10-07T10:02:00+08:00"},
            {"event_id": "evt-1", "track_no": "L-9", "x": -20.0, "y": 50.0,
             "sampled_at": "2026-10-07T10:00:00+08:00"},
            {"event_id": "evt-2", "track_no": "L-9", "x": 80.0, "y": 50.0,
             "sampled_at": "2026-10-07T10:00:10+08:00"},
        ]
    }
    status, body = call("POST", "/api/points", points)
    print("POST /api/points ->", status, "accepted:", body.get("accepted"))

    # 3) 同内容重传：幂等，不重复写入
    dup = {"event_id": "evt-1", "track_no": "L-9", "x": -20.0, "y": 50.0,
           "sampled_at": "2026-10-07T10:00:00+08:00"}
    status, body = call("POST", "/api/points", dup)
    print("POST /api/points (重传) ->", status, "duplicates:", body.get("duplicates"))

    # 4) 同事件号异内容：409 拒绝
    bad = {"event_id": "evt-1", "track_no": "L-9", "x": -19.0, "y": 50.0,
           "sampled_at": "2026-10-07T10:00:00+08:00"}
    print("POST /api/points (冲突) ->", call("POST", "/api/points", bad)[0])

    # 5) 查询穿越复核结果：内孔两侧两个片段 + 一个超阈值未覆盖时段
    status, body = call("GET", "/api/tracks/L-9/intrusions")
    print("GET  /api/tracks/L-9/intrusions ->", status)
    for seg in body["segments"]:
        print(f"  禁入片段 [{seg['zone_code']}] {seg['enter_at']} -> {seg['exit_at']}")
    for gap in body["uncovered_gaps"]:
        print(f"  未覆盖时段 {gap['from_at']} -> {gap['to_at']} ({gap['interval_seconds']}s)")

    # 6) 补录中间点后重查：未覆盖时段被新的相邻线段取代
    fill = {"event_id": "evt-2b", "track_no": "L-9", "x": 140.0, "y": 50.0,
            "sampled_at": "2026-10-07T10:00:40+08:00"}
    call("POST", "/api/points", fill)
    status, body = call("GET", "/api/tracks/L-9/intrusions")
    print("补录后重查 -> 未覆盖时段数:", len(body["uncovered_gaps"]),
          "片段数:", len(body["segments"]))


if __name__ == "__main__":
    main()
