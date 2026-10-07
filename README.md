# 野外巡线禁入区穿越复核服务

本地部署的 HTTP 服务：录入**带内孔、带生效/失效时刻**的禁入区与**带时区采样时刻**的
巡线定位点，按采样时间匀速连接轨迹，输出复核人员进入/离开禁入区的时序片段；
对相邻采样点间隔超阈值的时段输出「未覆盖」，不推测路径。

- 技术栈：Python 3.11+ / FastAPI / SQLite（WAL 模式，零外部依赖）
- 坐标：平面米坐标（笛卡尔，非经纬度）
- 几何：内置简单多边形（外边界 + 多个内孔）校验与线段裁切，不依赖 GIS 库

## 一、快速启动

### Docker Compose（一键启动并初始化数据库）

```bash
docker compose up -d --build
```

首次启动自动建表（`zones` / `points`），SQLite 数据文件位于命名卷 `patrol-data`
（容器内 `/app/data/patrol.db`）。健康检查：

```bash
curl http://127.0.0.1:8000/health
```

停止 / 清空数据：

```bash
docker compose down            # 停止（保留数据）
docker compose down -v         # 停止并删除数据卷
```

### 本地直接运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API 地址：`http://127.0.0.1:8000`
- Swagger 交互文档：`http://127.0.0.1:8000/docs`
- ReDoc：`http://127.0.0.1:8000/redoc`

## 二、配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PATROL_DB_PATH` | `data/patrol.db` | SQLite 数据库文件路径（目录自动创建） |
| `PATROL_GAP_THRESHOLD` | `300` | 相邻定位点最大允许间隔（秒）；可在查询接口按请求覆盖 |

## 三、核心规则

1. **轨迹连接**：点按「轨迹号」分组，乱序到达入库；查询时按采样时刻升序连接相邻点。
   假定相邻点间沿直线**匀速移动**；相邻点间隔超过阈值（默认 300s）时，
   该时段输出 `kind=uncovered`，不推测路径。
2. **时间区间左闭右开**：区域有效期 `[valid_from, valid_until)`；
   输出片段 `[start, end)`；失效时刻本身不计入。
3. **边界计入禁入，单点接触不形成片段**：沿边界移动算禁入；
   轨迹仅在一个顶点/一个时刻触碰区域（时长为 0）不产生片段。
4. **内孔**：孔边界与外边界同属面边界（计入禁入），孔的严格内部扣除。
   穿越内孔时线段被完整拆分为 `禁入 → 孔内(非禁入) → 禁入`。
5. **多区域**：同一线段命中多个区域时，按所有切点原子拆分，
   片段以 `zone_ids` 数组标注当时命中的全部区域。
6. **录入约束（定位点）**：
   - `event_id`（事件号）为幂等键：同事件号同内容重传不重复入库；
   - 同事件号异内容 → `409` 拒绝；
   - 同轨迹同一采样时刻异坐标 → `409` 拒绝；
   - 批量请求为整批原子事务，任一条冲突则整批不落库。
7. **无效多边形拒绝且不改原数据**：自交/共线回折、面积为 0、
   孔洞越界或触碰外边界、孔洞相互交叠/嵌套均返回 `422`，几何校验在写事务之前完成。
8. **快照一致性**：一次查询在同一个只读事务、同一份 WAL 快照中读取区域与轨迹点；
   补录点后再次查询即可反映新的相邻线段。
9. **时区**：所有输入时刻必须带时区偏移（如 `+08:00`），内部统一存 UTC；
   响应时间为 UTC（ISO 8601，`Z` 结尾）。

## 四、HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| POST | `/zones` | 新建禁入区（含内孔与有效期） |
| GET | `/zones` | 列出全部禁入区 |
| GET | `/zones/{zone_id}` | 查询单个禁入区 |
| POST | `/points` | 批量录入定位点（可乱序、幂等） |
| GET | `/trajectories` | 列出轨迹（点数、起止时刻） |
| GET | `/trajectories/{id}/points` | 查询轨迹的定位点（按采样时刻排序） |
| GET | `/trajectories/{id}/segments` | **复核结果：禁入片段与未覆盖时段** |

`GET /segments` 查询参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `gap_threshold` | 否 | 覆盖默认 300s 阈值（秒，> 0） |
| `start` / `end` | 否 | 查询窗口（带时区 ISO8601，左闭右开） |

输出片段对象：

```json
{
  "start": "2026-10-07T01:00:10Z",
  "end":   "2026-10-07T01:00:50Z",
  "kind":  "forbidden",          // forbidden=禁入片段；uncovered=未覆盖时段
  "zone_ids": [1]                // 同时命中的区域编号列表；uncovered 时为空
}
```

## 五、调用示例

完整脚本见 [`examples/`](examples/)：

```bash
# curl 版（含乱序录入、幂等重传、冲突拒绝、自交多边形拒绝、时间窗查询）
bash examples/curl_examples.sh

# Python 版（仅标准库）
python examples/python_client.py
```

最小调用：

```bash
# 1. 录入禁入区：100m×100m，中央 20m×20m 内孔，08:00-18:00(+08) 有效
curl -X POST http://127.0.0.1:8000/zones \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "A区",
        "outer_ring": [[0,0],[100,0],[100,100],[0,100]],
        "holes": [[[40,40],[60,40],[60,60],[40,60]]],
        "valid_from":  "2026-10-07T08:00:00+08:00",
        "valid_until": "2026-10-07T18:00:00+08:00"
      }'

# 2. 录入定位点（可乱序）
curl -X POST http://127.0.0.1:8000/points \
  -H 'Content-Type: application/json' \
  -d '{"points":[
    {"event_id":"EV-002","trajectory_id":"TRAJ-01","x":110,"y":50,"ts":"2026-10-07T09:02:00+08:00"},
    {"event_id":"EV-001","trajectory_id":"TRAJ-01","x":-10,"y":50,"ts":"2026-10-07T09:00:00+08:00"},
    {"event_id":"EV-003","trajectory_id":"TRAJ-01","x":120,"y":50,"ts":"2026-10-07T09:20:00+08:00"}
  ]}'

# 3. 查询复核结果
curl 'http://127.0.0.1:8000/trajectories/TRAJ-01/segments?gap_threshold=300'
```

上例（09:00 在 x=-10，09:02 在 x=110，1m/s 匀速横穿含孔区域；
之后 18 分钟无点）返回：

```json
[
  {"start": "2026-10-07T01:00:10Z", "end": "2026-10-07T01:00:50Z", "kind": "forbidden", "zone_ids": [1]},
  {"start": "2026-10-07T01:01:10Z", "end": "2026-10-07T01:01:50Z", "kind": "forbidden", "zone_ids": [1]},
  {"start": "2026-10-07T01:02:00Z", "end": "2026-10-07T01:20:00Z", "kind": "uncovered", "zone_ids": []}
]
```

（09:00+08 = 01:00Z；前两段为内孔两侧的禁入区间，第三段为超阈值未覆盖。）

## 六、测试

```bash
pip install -r requirements.txt
python -m pytest -q
```

覆盖：自交/退化/孔越界/孔嵌套拒绝、孔边界语义、内孔拆分、顶点点接触不成片段、
有效期与查询窗裁切、匀速插值、超阈值缺口与相邻缺口不合并、多区域原子拆分、
事件号幂等与两类冲突、整批原子性、乱序录入与补录后重查。

## 七、目录结构

```
app/
  geometry.py    # 多边形校验（自交/内孔）与线段×带孔面裁切
  analysis.py    # 轨迹连接、匀速插值、有效期裁切、多区域原子拆分
  db.py          # SQLite 连接、写事务（BEGIN IMMEDIATE）与快照事务
  schemas.py     # Pydantic 模型（时区校验、UTC 转换）
  main.py        # FastAPI 路由
examples/        # curl / Python 调用示例
tests/           # 几何、分析与端到端 API 测试
Dockerfile
docker-compose.yml
```
