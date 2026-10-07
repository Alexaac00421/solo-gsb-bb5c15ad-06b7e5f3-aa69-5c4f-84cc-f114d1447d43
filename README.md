# 野外巡线禁入区穿越复核服务

本地 HTTP 服务：录入**带生效/失效时刻、可含内孔**的禁入区，以及可**乱序到达**的轨迹定位点，
按采样时刻连接相邻定位点并按**线段匀速移动**插值，复核人员何时穿越了禁入区。

技术栈：Python 3.12 · FastAPI · SQLite（WAL 模式）· Shapely（平面米坐标几何计算）。

---

## 1. 一键启动

```bash
docker compose up -d --build
```

容器启动时自动创建数据库文件与表结构（幂等），数据保存在命名卷 `patrol-data`（容器内 `/data/patrol.db`）。

- 服务地址：`http://127.0.0.1:8080`
- 健康检查：`GET http://127.0.0.1:8080/health`
- 交互式文档（Swagger）：`http://127.0.0.1:8080/docs`
- OpenAPI 描述：`http://127.0.0.1:8080/openapi.json`

停止：`docker compose down`（数据保留；加 `-v` 可同时删除数据卷）。

### 不用 Docker 时

```bash
pip install -r requirements.txt
DB_PATH=./data/patrol.db MAX_GAP_SECONDS=60 python -m app
# 同样监听 0.0.0.0:8080 并自动初始化数据库
```

### 运行调用示例

```bash
python examples/call_api.py
# 可选：BASE_URL=http://其它主机:8080 python examples/call_api.py
```

### 运行测试

```bash
pip install -r requirements.txt pytest httpx
pytest
```

---

## 2. 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_HOST` | `0.0.0.0` | 监听地址 |
| `APP_PORT` | `8080` | 监听端口 |
| `DB_PATH` | `/data/patrol.db` | SQLite 数据库文件路径，启动时自动建库建表 |
| `MAX_GAP_SECONDS` | `60` | 相邻定位点允许的**最大采样间隔（秒，含）**；间隔超过该值时不推测路径，输出未覆盖时段 |
| `BUSY_TIMEOUT_MS` | `5000` | SQLite 写锁等待超时（毫秒） |

> 修改 `MAX_GAP_SECONDS` 后重启服务生效；查询是即时计算，历史点无需重算。

---

## 3. 数据模型与业务规则

- 所有坐标为**平面米坐标** `[x, y]`；外环首点无需重复闭合，允许重复闭合点。
- 所有时刻必须**带时区偏移**（如 `+08:00`、`Z`）；服务端统一按 UTC 微秒存储，响应输出 `...Z`。
- 时间区间一律**左闭右开** `[from, to)`；区域边界**计入**禁入；**单点接触不形成片段**。
- 禁入区：唯一编号 `zone_code`，外环 + 0..n 个内孔 + 生效/失效时刻。
  **无效或自交多边形一律 422 拒绝**，拒绝时不改动任何已有数据（`PUT` 为整体替换）。
- 定位点：轨迹号 `track_no`、事件号 `event_id`、坐标、带时区采样时刻。
  - 可**乱序到达**：分析时按 `(轨迹号, 采样时刻)` 排序连接；
  - **同事件号 + 完全相同内容**重传：幂等去重（返回 `duplicates`），不重复写入；
  - **同事件号但内容不同**（轨迹/坐标/时刻任一不一致）：`409` 拒绝，整批回滚；
  - **同轨迹 + 同采样时刻出现两个点**（坐标相同或不同）：`409` 拒绝，整批回滚；
  - 间隔 `> MAX_GAP_SECONDS` 的相邻点之间输出**未覆盖时段**，不连接、不推测路径；
  - **补录点后重新查询**，相邻线段自动重新连接，立即反映新结果；
  - 一次查询内区域表与点表的读取处于同一 SQLite 读事务，基于**同一数据快照**。
- 同一线段：
  - 穿过**内孔**时按内孔边界完整拆分为多个片段（孔内不算禁入）；
  - 同时穿越**多个区域**时每个区域各自输出完整片段，并以 `zone_code` 标注；
  - 片段与区域有效时段取交集裁切。
- 仅在采样点处首尾相接的同一区域连续片段会合并；内孔/区外造成的拆分保持完整。

---

## 4. HTTP API

### `PUT /api/zones` — 新增/整体替换禁入区

```bash
curl -X PUT http://127.0.0.1:8080/api/zones \
  -H 'Content-Type: application/json' \
  -d '{
    "zone_code": "ZONE-001",
    "outer_ring": [[0,0],[100,0],[100,100],[0,100]],
    "holes": [[[40,40],[60,40],[60,60],[40,60]]],
    "valid_from": "2026-10-07T08:00:00+08:00",
    "valid_to":   "2026-10-07T18:00:00+08:00"
  }'
```

无效/自交多边形、`valid_to <= valid_from`、无时区时间均返回 `422`，原数据不变。

### `GET /api/zones` — 列出全部禁入区

### `POST /api/points` — 录入定位点（单个或批量，可乱序）

单个点对象，或批量包体 `{"points": [ ... ]}` 均可：

```bash
curl -X POST http://127.0.0.1:8080/api/points \
  -H 'Content-Type: application/json' \
  -d '{
    "points": [
      {"event_id":"evt-3","track_no":"L-9","x":180.0,"y":50.0,
       "sampled_at":"2026-10-07T10:02:00+08:00"},
      {"event_id":"evt-1","track_no":"L-9","x":-20.0,"y":50.0,
       "sampled_at":"2026-10-07T10:00:00+08:00"}
    ]
  }'
```

返回 `{"accepted": n, "duplicates": m, "points": [...]}`；冲突返回 `409` 且整批回滚。

### `GET /api/tracks/{track_no}/intrusions` — 穿越复核

```bash
curl 'http://127.0.0.1:8080/api/tracks/L-9/intrusions'
```

响应：

```json
{
  "track_no": "L-9",
  "point_count": 3,
  "segments": [
    {
      "zone_code": "ZONE-001",
      "enter_at": "2026-10-07T02:00:02.000000Z",
      "exit_at":  "2026-10-07T02:00:06.000000Z",
      "enter_coord": [0.0, 50.0],
      "exit_coord":  [40.0, 50.0]
    },
    {
      "zone_code": "ZONE-001",
      "enter_at": "2026-10-07T02:00:08.000000Z",
      "exit_at":  "2026-10-07T02:00:10.000000Z",
      "enter_coord": [60.0, 50.0],
      "exit_coord":  [80.0, 50.0]
    }
  ],
  "uncovered_gaps": [
    {
      "from_at": "2026-10-07T02:00:10.000000Z",
      "to_at":   "2026-10-07T02:02:00.000000Z",
      "from_coord": [80.0, 50.0],
      "to_coord":   [180.0, 50.0],
      "interval_seconds": 110.0
    }
  ]
}
```

- `segments` 按进入时刻升序（同时刻按区域编号），字段为区域编号、进入/离开时刻与插值坐标；
- `uncovered_gaps` 为超过 `MAX_GAP_SECONDS` 的相邻点时段（路径未知，不做任何推测）。

### `GET /health` — 健康检查

---

## 5. 目录结构

```
app/
  config.py       # 环境变量配置
  database.py     # SQLite 连接、建库、写事务/快照读事务
  geometry.py     # 多边形校验（拒绝自交）、线段×区域求交
  models.py       # 请求/响应模型（时间强制带时区）
  repository.py   # 区域/定位点 DAO，幂等与冲突规则
  analysis.py     # 匀速插值、内孔/多区域拆分、时段裁切、缺口识别
  api.py          # FastAPI 路由
  __main__.py     # python -m app 入口（建库 + uvicorn）
examples/call_api.py   # 纯标准库调用示例
tests/                 # pytest 端到端与快照隔离测试（35 项）
Dockerfile / docker-compose.yml
```
