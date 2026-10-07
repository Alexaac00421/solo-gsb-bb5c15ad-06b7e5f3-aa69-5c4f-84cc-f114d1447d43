#!/usr/bin/env python3
"""HTTP API 调用示例（标准库 urllib，无需第三方依赖）。

用法：
    python examples/python_client.py [http://127.0.0.1:8000]
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def call(method: str, path: str, payload=None, params=None):
    url = BASE + path
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


def main() -> None:
    print("health:", call("GET", "/health"))

    status, zone = call("POST", "/zones", {
        "name": "A区-含内孔",
        "outer_ring": [[0, 0], [100, 0], [100, 100], [0, 100]],
        "holes": [[[40, 40], [60, 40], [60, 60], [40, 60]]],
        "valid_from": "2026-10-07T08:00:00+08:00",
        "valid_until": "2026-10-07T18:00:00+08:00",
    })
    print("create zone:", status, "zone_id =", zone["zone_id"])

    # 乱序点：EV-2 先发，EV-1 后发
    status, res = call("POST", "/points", {"points": [
        {"event_id": "EV-002", "trajectory_id": "TRAJ-01",
         "x": 110, "y": 50, "ts": "2026-10-07T09:02:00+08:00"},
        {"event_id": "EV-001", "trajectory_id": "TRAJ-01",
         "x": -10, "y": 50, "ts": "2026-10-07T09:00:00+08:00"},
        {"event_id": "EV-003", "trajectory_id": "TRAJ-01",
         "x": 120, "y": 50, "ts": "2026-10-07T09:20:00+08:00"},
    ]})
    print("ingest:", status, "accepted =", res["accepted"])

    # 幂等重传
    status, res = call("POST", "/points", {"points": [
        {"event_id": "EV-001", "trajectory_id": "TRAJ-01",
         "x": -10, "y": 50, "ts": "2026-10-07T09:00:00+08:00"}]})
    print("retransmit same content:", status, "accepted =", res["accepted"])

    # 异内容重传 -> 409
    status, err = call("POST", "/points", {"points": [
        {"event_id": "EV-001", "trajectory_id": "TRAJ-01",
         "x": 999, "y": 999, "ts": "2026-10-07T09:00:00+08:00"}]})
    print("conflict status:", status, "detail:", err["detail"])

    # 查询复核结果
    status, segs = call(
        "GET", "/trajectories/TRAJ-01/segments",
        params={"gap_threshold": 300})
    print(f"segments ({status}):")
    for s in segs:
        print(f"  {s['kind']:9s} {s['start']} -> {s['end']} zones={s['zone_ids']}")


if __name__ == "__main__":
    main()
