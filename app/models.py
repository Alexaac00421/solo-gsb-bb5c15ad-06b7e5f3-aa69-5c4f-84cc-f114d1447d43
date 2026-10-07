"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from .timeutil import iso

# 响应中的时间一律输出带微秒与 UTC 的 Z 格式（缺失位补零）。
ApiDateTime = Annotated[
    datetime,
    PlainSerializer(lambda dt: iso(dt), return_type=str, when_used="json"),
]


class ZoneIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone_code: str = Field(min_length=1, description="区域编号（唯一业务键）")
    outer_ring: list[tuple[float, float]] = Field(
        min_length=3, description="外环顶点（平面米坐标），首尾重复点可省略"
    )
    holes: list[list[tuple[float, float]]] = Field(
        default_factory=list, description="内孔顶点列表"
    )
    valid_from: datetime = Field(description="生效时刻，必须带时区")
    valid_to: datetime = Field(description="失效时刻，必须带时区；时间区间左闭右开")

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("时间必须带时区偏移，例如 2026-10-07T08:00:00+08:00")
        return v


class ZoneOut(BaseModel):
    zone_code: str
    outer_ring: list[tuple[float, float]]
    holes: list[list[tuple[float, float]]]
    valid_from: ApiDateTime
    valid_to: ApiDateTime


class PointIn(BaseModel):
    model_config = ConfigDict(frozen=True, json_schema_extra={"format": "json"})

    track_no: str = Field(min_length=1, description="轨迹号")
    event_id: str = Field(min_length=1, description="事件号（唯一，幂等键）")
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    sampled_at: datetime = Field(description="带时区采样时刻")

    @field_validator("sampled_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("时间必须带时区偏移，例如 2026-10-07T08:00:00+08:00")
        return v


class PointBatchIn(BaseModel):
    points: list[PointIn] = Field(min_length=1)


class PointResult(BaseModel):
    event_id: str
    track_no: str
    x: float
    y: float
    sampled_at: ApiDateTime


class PointsIngestResult(BaseModel):
    accepted: int
    duplicates: int
    points: list[PointResult]


class ForbiddenSegment(BaseModel):
    zone_code: str
    enter_at: ApiDateTime
    exit_at: ApiDateTime
    enter_coord: tuple[float, float]
    exit_coord: tuple[float, float]


class UncoveredGap(BaseModel):
    from_at: ApiDateTime
    to_at: ApiDateTime
    from_coord: tuple[float, float]
    to_coord: tuple[float, float]
    interval_seconds: float


class IntrusionsOut(BaseModel):
    track_no: str
    point_count: int
    segments: list[ForbiddenSegment]
    uncovered_gaps: list[UncoveredGap]


class ZonesListOut(BaseModel):
    zones: list[ZoneOut]


class ErrorOut(BaseModel):
    detail: str
