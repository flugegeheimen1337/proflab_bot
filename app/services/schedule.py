from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services.errors import ScheduleError

WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
}


@dataclass(frozen=True)
class Recurrence:
    recurrence: str
    weekday: int | None
    hour: int
    minute: int


def _clock(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as exc:
        raise ScheduleError("format") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleError("format")
    return hour, minute


def parse_run_at(raw: str, tz_name: str, now: datetime) -> datetime:
    try:
        local = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ScheduleError("format") from exc
    utc = local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if utc <= current.astimezone(timezone.utc):
        raise ScheduleError("past")
    return utc


def parse_recurrence(raw: str) -> Recurrence:
    parts = raw.strip().lower().split()
    if len(parts) == 2 and parts[0] == "daily":
        hour, minute = _clock(parts[1])
        return Recurrence("daily", None, hour, minute)
    if len(parts) == 3 and parts[0] == "weekly":
        if parts[1] not in WEEKDAYS:
            raise ScheduleError("weekday")
        hour, minute = _clock(parts[2])
        return Recurrence("weekly", WEEKDAYS[parts[1]], hour, minute)
    raise ScheduleError("format")


def due_slot(
    *,
    recurrence: str | None,
    weekday: int | None,
    hour: int | None,
    minute: int | None,
    tz_name: str,
    now: datetime,
) -> str | None:
    if recurrence not in {"daily", "weekly"} or hour is None or minute is None:
        return None
    if recurrence == "weekly" and weekday is None:
        return None
    local = now.astimezone(ZoneInfo(tz_name))
    if recurrence == "weekly" and local.weekday() != weekday:
        return None
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = (local - scheduled).total_seconds()
    if not 0 <= delta < 600:
        return None
    return scheduled.strftime("%Y-%m-%d")
