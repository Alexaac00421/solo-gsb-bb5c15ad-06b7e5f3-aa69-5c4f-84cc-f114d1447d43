"""Pydantic 请求/响应模型。时刻必须带时区偏移，内部统一转 UTC epoch 秒。"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def to_epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        raise ValueError("时刻必须带时区偏移（例如 2026-10-07T08:00:00+08:00）")
    return dt.astimezone(timezone.utc).timestamp()


def from_epoch(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class Coordinate(BaseModel):
    x: float = Field(..., description="平面米坐标 X")
    y: float = Field(..., description="平面米坐标 Y")


class ZoneCreate(BaseModel):
    outer_ring: list[tuple[float, float]] = Field(
        ..., min_length=3, description="外环顶点 [[x,y],...]，首尾点可省略")
    holes: list[list[tuple[float, float]]] = Field(
        default_factory=list, description="内孔顶点列表，每个孔同外环格式")
    valid_from: datetime = Field(..., description="生效时刻（含），必须带时区")
    valid_until: datetime = Field(..., description="失效时刻（不含），必须带时区")
    name: str | None = None

    @field_validator("valid_from", "valid_until")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("时刻必须带时区偏移")
        return v


class ZoneOut(BaseModel):
    zone_id: int
    name: str | None
    outer_ring: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    valid_from: datetime
    valid_until: datetime
    created_at: datetime


class PointIn(BaseModel):
    event_id: str = Field(..., min_length=1, description="事件号（幂等键）")
    trajectory_id: str = Field(..., min_length=1, description="轨迹号")
    x: float
    y: float
    ts: datetime = Field(..., description="带时区的采样时刻")

    @field_validator("ts")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("采样时刻必须带时区偏移")
        return v


class PointBatch(BaseModel):
    points: list[PointIn] = Field(..., min_length=1)


class PointOut(BaseModel):
    event_id: str
    trajectory_id: str
    x: float
    y: float
    ts: datetime


class BatchResult(BaseModel):
    accepted: int
    points: list[PointOut]


class Conflict(BaseModel):
    event_id: str
    reason: str
    existing: PointOut | None = None


class SegmentOut(BaseModel):
    start: datetime
    end: datetime
    kind: str
    zone_ids: list[int]


class TrajectoryOut(BaseModel):
    trajectory_id: str
    n_points: int
    first_ts: datetime | None
    last_ts: datetime | None
