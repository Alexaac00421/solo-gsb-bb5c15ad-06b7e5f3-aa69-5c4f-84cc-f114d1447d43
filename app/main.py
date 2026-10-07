"""野外巡线禁入区穿越复核 —— FastAPI 本地服务。"""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import ValidationError

from . import db
from .analysis import Segment, Zone, analyze, build_zone
from .geometry import GeometryError
from .schemas import (
    BatchResult,
    PointBatch,
    PointOut,
    SegmentOut,
    TrajectoryOut,
    ZoneCreate,
    ZoneOut,
    from_epoch,
    to_epoch,
)

DB_PATH = os.environ.get("PATROL_DB_PATH", "data/patrol.db")
DEFAULT_GAP_THRESHOLD = float(os.environ.get("PATROL_GAP_THRESHOLD", "300"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即初始化数据库（建表、开启 WAL）
    conn = db.connect(DB_PATH)
    db.init_db(conn)
    conn.close()
    yield


app = FastAPI(
    title="野外巡线禁入区穿越复核",
    version="1.0.0",
    description=(
        "录入带内孔与有效时段的禁入区及乱序到达的定位点，"
        "按采样时间匀速连接并裁切，输出禁入片段与未覆盖时段。"
    ),
    lifespan=lifespan,
)


def get_conn() -> Iterator[db.sqlite3.Connection]:
    # 每请求独立连接：查询在该连接的单个只读事务中读取同一快照，
    # 写事务用 BEGIN IMMEDIATE 与其他请求在文件层串行化（WAL）。
    conn = db.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ---------- 健康检查 ----------

@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


# ---------- 禁入区 ----------

def _zone_out(row) -> ZoneOut:
    return ZoneOut(
        zone_id=row["zone_id"],
        name=row["name"],
        outer_ring=json.loads(row["outer_ring"]),
        holes=json.loads(row["holes"]),
        valid_from=from_epoch(row["valid_from"]),
        valid_until=from_epoch(row["valid_until"]),
        created_at=from_epoch(row["created_at"]),
    )


@app.post("/zones", response_model=ZoneOut, status_code=201, tags=["zones"])
def create_zone(body: ZoneCreate, conn=Depends(get_conn)) -> ZoneOut:
    vf, vu = to_epoch(body.valid_from), to_epoch(body.valid_until)
    if vu <= vf:
        raise HTTPException(422, "valid_until 必须晚于 valid_from")
    # 先完成全部几何校验（失败仅抛异常，不触碰已存数据），再开写事务
    try:
        outer, holes = build_zone(body.outer_ring, body.holes, vf, vu)
    except GeometryError as exc:
        raise HTTPException(422, f"无效多边形：{exc}")

    with db.write_tx(conn):
        zone_id = db.insert_zone(conn, outer, holes, vf, vu, body.name)
        row = db.get_zone(conn, zone_id)
    return _zone_out(row)


@app.get("/zones", response_model=list[ZoneOut], tags=["zones"])
def zones(conn=Depends(get_conn)) -> list[ZoneOut]:
    with db.snapshot(conn):
        return [_zone_out(r) for r in db.list_zones(conn)]


@app.get("/zones/{zone_id}", response_model=ZoneOut, tags=["zones"])
def zone(zone_id: int, conn=Depends(get_conn)) -> ZoneOut:
    with db.snapshot(conn):
        row = db.get_zone(conn, zone_id)
    if row is None:
        raise HTTPException(404, f"区域 {zone_id} 不存在")
    return _zone_out(row)


# ---------- 定位点 ----------

def _point_out(row) -> PointOut:
    return PointOut(
        event_id=row["event_id"],
        trajectory_id=row["trajectory_id"],
        x=row["x"],
        y=row["y"],
        ts=from_epoch(row["ts"]),
    )


@app.post("/points", response_model=BatchResult, status_code=201, tags=["points"])
def ingest_points(body: PointBatch, conn=Depends(get_conn)) -> BatchResult:
    """批量录入。整批原子：任何一条冲突则全部不写入（原数据不变）。

    - 同事件号同内容重传：视为已接受，不重复计数；
    - 同事件号异内容、同轨迹同时刻异坐标：409 拒绝。
    """
    # 批内自检：事件号不重复（内容不同时）、同轨迹同时刻坐标一致
    seen_events: dict[str, object] = {}
    seen_slots: dict[tuple[str, float], tuple[float, float]] = {}
    for p in body.points:
        ep = to_epoch(p.ts)
        if p.event_id in seen_events and seen_events[p.event_id] != p:
            raise HTTPException(409, f"批内事件号 {p.event_id} 内容冲突")
        seen_events[p.event_id] = p
        key = (p.trajectory_id, round(ep, 6))
        if key in seen_slots and seen_slots[key] != (p.x, p.y):
            raise HTTPException(
                409, f"批内轨迹 {p.trajectory_id} 在同一采样时刻出现异坐标")
        seen_slots.setdefault(key, (p.x, p.y))

    accepted: list = []
    with db.write_tx(conn) as tx:
        for p in body.points:
            ep = to_epoch(p.ts)
            existing_ev = db.find_point_by_event(tx, p.event_id)
            if existing_ev is not None:
                same = (
                    existing_ev["trajectory_id"] == p.trajectory_id
                    and abs(existing_ev["ts"] - ep) < 1e-6
                    and existing_ev["x"] == p.x and existing_ev["y"] == p.y
                )
                if not same:
                    raise HTTPException(
                        409,
                        f"事件号 {p.event_id} 已存在但内容不同，拒绝",
                    )
                continue  # 幂等重传：不重复

            existing_slot = db.find_point_at_ts(tx, p.trajectory_id, ep)
            if existing_slot is not None and (
                existing_slot["event_id"] != p.event_id
                and (existing_slot["x"] != p.x or existing_slot["y"] != p.y)
            ):
                raise HTTPException(
                    409,
                    f"轨迹 {p.trajectory_id} 在 {p.ts.isoformat()} 已存在不同坐标的点 "
                    f"(事件号 {existing_slot['event_id']})，拒绝",
                )
            if existing_slot is not None and existing_slot["event_id"] != p.event_id:
                # 同轨迹同时刻同坐标但不同事件号：UNIQUE(trajectory_id,ts) 不允许
                raise HTTPException(
                    409,
                    f"轨迹 {p.trajectory_id} 在 {p.ts.isoformat()} 已有定位点"
                    f"(事件号 {existing_slot['event_id']})，拒绝重复时刻",
                )

            db.insert_point(tx, p.event_id, p.trajectory_id, p.x, p.y, ep)
            accepted.append(p)

    return BatchResult(
        accepted=len(accepted),
        points=[PointOut(
            event_id=p.event_id, trajectory_id=p.trajectory_id,
            x=p.x, y=p.y, ts=p.ts) for p in accepted],
    )


@app.get("/trajectories", response_model=list[TrajectoryOut], tags=["trajectories"])
def trajectories(conn=Depends(get_conn)) -> list[TrajectoryOut]:
    with db.snapshot(conn):
        rows = db.list_trajectories(conn)
    return [TrajectoryOut(
        trajectory_id=r["trajectory_id"], n_points=r["n_points"],
        first_ts=from_epoch(r["first_ts"]) if r["first_ts"] is not None else None,
        last_ts=from_epoch(r["last_ts"]) if r["last_ts"] is not None else None,
    ) for r in rows]


@app.get("/trajectories/{trajectory_id}/points",
         response_model=list[PointOut], tags=["trajectories"])
def trajectory_points(trajectory_id: str, conn=Depends(get_conn)) -> list[PointOut]:
    with db.snapshot(conn):
        rows = db.get_trajectory_points(conn, trajectory_id)
    return [_point_out(r) for r in rows]


# ---------- 穿越复核 ----------

def _seg_out(seg: Segment) -> SegmentOut:
    return SegmentOut(
        start=from_epoch(seg.start),
        end=from_epoch(seg.end),
        kind=seg.kind,
        zone_ids=seg.zone_ids,
    )


@app.get("/trajectories/{trajectory_id}/segments",
         response_model=list[SegmentOut], tags=["trajectories"])
def segments(
    trajectory_id: str,
    gap_threshold: float = Query(
        DEFAULT_GAP_THRESHOLD, gt=0,
        description="相邻采样点最大允许间隔（秒），超过则输出 uncovered"),
    start: str | None = Query(None, description="查询窗起始（含，带时区 ISO8601）"),
    end: str | None = Query(None, description="查询窗结束（不含，带时区 ISO8601）"),
    conn=Depends(get_conn),
) -> list[SegmentOut]:
    """基于同一数据快照计算；补录点后再次调用即反映新的相邻线段。"""
    win_lo = win_hi = None
    from datetime import datetime
    try:
        if start is not None:
            dt = datetime.fromisoformat(start)
            win_lo = to_epoch(dt)
        if end is not None:
            dt = datetime.fromisoformat(end)
            win_hi = to_epoch(dt)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"时间参数无效：{exc}")
    if win_lo is not None and win_hi is not None and win_hi <= win_lo:
        raise HTTPException(422, "end 必须晚于 start")

    with db.snapshot(conn) as tx:  # 点与区域在同一事务同一快照读取
        pts = db.get_trajectory_points(tx, trajectory_id)
        if not pts:
            raise HTTPException(404, f"轨迹 {trajectory_id} 无定位点")
        zones = [Zone.from_row(r) for r in db.list_zones(tx)]

    result = analyze(pts, zones, gap_threshold, (win_lo, win_hi))
    return [_seg_out(s) for s in result]
