"""The clinic's working day, in its own time zone. Pure functions, no I/O."""

from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

SLOT_LENGTH: Final = timedelta(minutes=30)
# Morning and afternoon sessions, local clinic time; closed for lunch 13:00-14:00.
SESSIONS: Final = ((time(9), time(13)), (time(14), time(19)))


def slot_starts(day: date, timezone: str) -> list[datetime]:
    """Start of every 30-minute slot on `day`, as timezone-aware datetimes in clinic time."""
    zone = ZoneInfo(timezone)
    starts: list[datetime] = []
    for opens, closes in SESSIONS:
        start = datetime.combine(day, opens, tzinfo=zone)
        end = datetime.combine(day, closes, tzinfo=zone)
        while start + SLOT_LENGTH <= end:
            starts.append(start)
            start += SLOT_LENGTH
    return starts


def day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    """[start, end) of `day` in clinic time, for `starts_at >= start AND starts_at < end`."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def local_date(instant: datetime, timezone: str) -> date:
    """Calendar date of an instant at the clinic."""
    return instant.astimezone(ZoneInfo(timezone)).date()
