from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def bounds(tz_name: str, moment: datetime, *, days: int = 1, month: bool = False) -> tuple[datetime, datetime]:
    """Return [start, end) in UTC for a local day, a rolling window, or a calendar month."""
    local = as_utc(moment).astimezone(ZoneInfo(tz_name))
    if month:
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if days == 1:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    end = as_utc(moment)
    start = end - timedelta(days=days)
    return start, end
