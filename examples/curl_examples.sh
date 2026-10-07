#!/usr/bin/env bash
# 野外巡线禁入区穿越复核 —— HTTP API 调用示例（curl）
# 服务默认地址：http://127.0.0.1:8000 ；交互文档：/docs
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"

echo "== 健康检查 =="
curl -sS "$BASE/health"; echo

echo
echo "== 1) 录入禁入区：100m×100m 矩形，中央 20m×20m 内孔，08:00-18:00(+08) 有效 =="
curl -sS -X POST "$BASE/zones" \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "A区-含内孔",
        "outer_ring": [[0,0],[100,0],[100,100],[0,100]],
        "holes": [[[40,40],[60,40],[60,60],[40,60]]],
        "valid_from":  "2026-10-07T08:00:00+08:00",
        "valid_until": "2026-10-07T18:00:00+08:00"
      }'; echo

echo
echo "== 2) 乱序录入定位点（允许整批乱序，按采样时刻自动排序） =="
curl -sS -X POST "$BASE/points" \
  -H 'Content-Type: application/json' \
  -d '{
        "points": [
          {"event_id":"EV-002","trajectory_id":"TRAJ-01","x":110,"y":50,"ts":"2026-10-07T09:02:00+08:00"},
          {"event_id":"EV-001","trajectory_id":"TRAJ-01","x":-10,"y":50,"ts":"2026-10-07T09:00:00+08:00"},
          {"event_id":"EV-003","trajectory_id":"TRAJ-01","x":120,"y":50,"ts":"2026-10-07T09:20:00+08:00"}
        ]
      }'; echo

echo
echo "== 3) 同事件号同内容重传（幂等：accepted=0） =="
curl -sS -X POST "$BASE/points" \
  -H 'Content-Type: application/json' \
  -d '{"points":[{"event_id":"EV-001","trajectory_id":"TRAJ-01","x":-10,"y":50,"ts":"2026-10-07T09:00:00+08:00"}]}'; echo

echo
echo "== 4) 异内容重传（409 拒绝） =="
curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST "$BASE/points" \
  -H 'Content-Type: application/json' \
  -d '{"points":[{"event_id":"EV-001","trajectory_id":"TRAJ-01","x":999,"y":999,"ts":"2026-10-07T09:00:00+08:00"}]}'

echo
echo "== 5) 查询穿越片段（阈值 300s；内孔拆两段，超阈值缺口输出 uncovered） =="
curl -sS "$BASE/trajectories/TRAJ-01/segments?gap_threshold=300" | python3 -m json.tool

echo
echo "== 6) 按时间窗查询（左闭右开） =="
curl -sS "$BASE/trajectories/TRAJ-01/segments" \
  --data-urlencode "start=2026-10-07T09:00:30+08:00" \
  --data-urlencode "end=2026-10-07T09:05:00+08:00" -G | python3 -m json.tool

echo
echo "== 7) 无效（自交）多边形被拒绝，HTTP 422 =="
curl -sS -o /tmp/z.json -w "HTTP %{http_code} " -X POST "$BASE/zones" \
  -H 'Content-Type: application/json' \
  -d '{
        "outer_ring": [[0,0],[100,100],[100,0],[0,80]],
        "holes": [],
        "valid_from":  "2026-10-07T08:00:00+08:00",
        "valid_until": "2026-10-07T18:00:00+08:00"
      }'
cat /tmp/z.json; echo
