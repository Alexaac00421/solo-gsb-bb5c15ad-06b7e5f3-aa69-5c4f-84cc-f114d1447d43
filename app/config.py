"""Runtime configuration, read from environment variables.

All thresholds are in seconds. Times presented to/by the HTTP API carry a
timezone; the database stores them as UTC integer microseconds since epoch.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 不是合法数字: {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"环境变量 {name} 不能为负数: {value}")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"环境变量 {name} 不是合法整数: {raw!r}") from exc
    if value < 0:
        raise RuntimeError(f"环境变量 {name} 不能为负数: {value}")
    return value


@dataclass(frozen=True)
class Settings:
    db_path: str
    # 相邻定位点的最大允许采样间隔；超过该值的相邻点之间输出“未覆盖时段”
    max_gap_seconds: float
    busy_timeout_ms: int
    host: str
    port: int


def get_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("DB_PATH", "/data/patrol.db"),
        max_gap_seconds=_env_float("MAX_GAP_SECONDS", 60.0),
        busy_timeout_ms=_env_int("BUSY_TIMEOUT_MS", 5000),
        host=os.environ.get("APP_HOST", "0.0.0.0"),
        port=_env_int("APP_PORT", 8080),
    )
