"""HTTP API for forbidden zones and patrol-track crossing review."""
from __future__ import annotations

from contextlib import asynccontextmanager

import math

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import repository
from .analysis import analyze_track
from .config import get_settings
from .database import connect, init_db, snapshot, write_transaction
from .geometry import GeometryError, build_polygon
from .models import (
    ForbiddenSegment,
    IntrusionsOut,
    PointBatchIn,
    PointIn,
    PointResult,
    PointsIngestResult,
    UncoveredGap,
    ZoneIn,
    ZoneOut,
    ZonesListOut,
)
from .repository import PointConflict
from .timeutil import from_us, to_us


def get_db(request: Request):
    """One connection per request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 容器启动即创建数据库文件与表结构。
    init_db()
    yield


app = FastAPI(
    title="野外巡线禁入区穿越复核 API",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "录入带生效/失效时刻、可含内孔的禁入区，与可乱序到达的轨迹定位点；"
        "按采样时间连接相邻点并匀速插值，输出禁入片段与未覆盖时段。"
    ),
)


def _sanitize(value):
    """把 NaN/Infinity 替换为 None，保证校验错误本身也能被 JSON 序列化。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": jsonable_encoder(_sanitize(exc.errors()))},
    )


def _zone_out(row: dict) -> ZoneOut:
    return ZoneOut(
        zone_code=row["zone_code"],
        outer_ring=[tuple(c) for c in row["outer_ring"]],
        holes=[[tuple(c) for c in h] for h in row["holes"]],
        valid_from=from_us(row["valid_from_us"]),
        valid_to=from_us(row["valid_to_us"]),
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.put("/api/zones", response_model=ZoneOut, status_code=status.HTTP_200_OK)
def put_zone(payload: ZoneIn, db=Depends(get_db)) -> ZoneOut:
    """新增或整体替换一个禁入区。

    多边形先完整校验（自交、退化、内孔越界等一律拒绝）；校验失败时不会
    改动任何已有数据。
    """
    vf_us, vt_us = to_us(payload.valid_from), to_us(payload.valid_to)
    if vt_us <= vf_us:
        raise HTTPException(status_code=422, detail="valid_to 必须晚于 valid_from（时间区间左闭右开）")

    outer = [[x, y] for x, y in payload.outer_ring]
    holes = [[[x, y] for x, y in ring] for ring in payload.holes]
    try:
        build_polygon(outer, holes)  # 仅做校验；入库内容用原始 JSON
    except GeometryError as exc:
        raise HTTPException(status_code=422, detail=f"无效多边形，已拒绝且不改动原数据: {exc}") from exc

    with write_transaction(db):
        repository.upsert_zone(
            db,
            zone_code=payload.zone_code,
            outer_ring=outer,
            holes=holes,
            valid_from_us=vf_us,
            valid_to_us=vt_us,
        )
        row = repository.get_zone(db, payload.zone_code)
    return _zone_out(row)


@app.get("/api/zones", response_model=ZonesListOut)
def list_all_zones(db=Depends(get_db)) -> ZonesListOut:
    with snapshot(db):
        rows = repository.list_zones(db)
    return ZonesListOut(zones=[_zone_out(r) for r in rows])


@app.post("/api/points", response_model=PointsIngestResult, status_code=status.HTTP_201_CREATED)
def ingest_points(raw_body: dict, db=Depends(get_db)) -> PointsIngestResult:
    """录入定位点（可乱序）。请求体支持单个点对象或 {"points": [...]} 批量。

    幂等规则：
    - 同事件号且内容完全相同 -> 视为重传，计数为 duplicates；
    - 同事件号但内容不同 -> 409 拒绝，整批回滚；
    - 同轨迹同采样时刻出现两个点（即使坐标相同）-> 409 拒绝，整批回滚。
    """
    # 先解析（422 校验失败时绝不触碰数据库），再开写事务。
    try:
        if "points" in raw_body:
            parsed = PointBatchIn.model_validate(raw_body)
            points = parsed.points
        else:
            points = [PointIn.model_validate(raw_body)]
    except ValidationError as exc:
        # 与 FastAPI 声明式入参保持一致：非法 JSON 模型返回 422。
        raise RequestValidationError(exc.errors()) from exc

    with write_transaction(db):
        try:
            counts = repository.insert_points(db, points)
        except PointConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        accepted_events = {p.event_id for p in points}
        placeholders = ",".join("?" for _ in accepted_events)
        rows = db.execute(
            f"""SELECT event_id, track_no, x, y, sampled_at_us FROM points
                 WHERE event_id IN ({placeholders})
                 ORDER BY sampled_at_us, event_id""",
            tuple(accepted_events),
        ).fetchall()

    return PointsIngestResult(
        accepted=counts["accepted"],
        duplicates=counts["duplicates"],
        points=[
            PointResult(
                event_id=r["event_id"],
                track_no=r["track_no"],
                x=r["x"],
                y=r["y"],
                sampled_at=from_us(r["sampled_at_us"]),
            )
            for r in rows
        ],
    )


@app.get("/api/tracks/{track_no}/intrusions", response_model=IntrusionsOut)
def track_intrusions(track_no: str, db=Depends(get_db)) -> IntrusionsOut:
    """基于同一数据快照计算某条轨迹的禁入穿越片段与未覆盖时段。

    补录点之后重新调用本接口，相邻线段会自动重新连接并反映新结果。
    """
    settings = get_settings()
    with snapshot(db):
        result = analyze_track(db, track_no, settings.max_gap_seconds)

    segments = [
        ForbiddenSegment(
            zone_code=iv.zone_code,
            enter_at=from_us(iv.enter_us),
            exit_at=from_us(iv.exit_us),
            enter_coord=(round(iv.enter_coord[0], 6), round(iv.enter_coord[1], 6)),
            exit_coord=(round(iv.exit_coord[0], 6), round(iv.exit_coord[1], 6)),
        )
        for iv in result["segments"]
    ]
    gaps = [
        UncoveredGap(
            from_at=from_us(g.from_us),
            to_at=from_us(g.to_us),
            from_coord=(round(g.from_coord[0], 6), round(g.from_coord[1], 6)),
            to_coord=(round(g.to_coord[0], 6), round(g.to_coord[1], 6)),
            interval_seconds=round((g.to_us - g.from_us) / 1_000_000.0, 6),
        )
        for g in result["gaps"]
    ]
    return IntrusionsOut(
        track_no=result["track_no"],
        point_count=result["point_count"],
        segments=segments,
        uncovered_gaps=gaps,
    )
