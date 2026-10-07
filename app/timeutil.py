"""Time helpers: timezone-aware datetimes <-> UTC microseconds since epoch."""
from __future__ import annotations

from datetime import datetime, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def to_us(dt: datetime) -> int:
    """Convert a timezone-aware datetime to UTC microseconds since epoch."""
    if dt.tzinfo is None:
        raise ValueError("时间必须带时区信息")
    return round((dt.astimezone(timezone.utc) - _EPOCH).total_seconds() * 1_000_000)


def from_us(us: int) -> datetime:
    """Rebuild a timezone-aware (UTC) datetime from stored microseconds."""
    return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)


def iso(dt: datetime) -> str:
    """ISO-8601 UTC string, always with microseconds and a trailing Z."""
    text = dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
    # fromtimestamp 已产生 +00:00；兼容任何 UTC 变体写法。
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text
