"""When a booking needs front-desk approval (CFD-15). Pure functions, the clinic's own time zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from clinic.domain.policies import has_started, is_same_day, requires_approval

TZ = "Asia/Muscat"  # UTC+4
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # Monday 12:00 at the clinic


def _local(day: int, hour: int, minute: int = 0) -> datetime:
    """A November 2026 clinic-time instant."""
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo(TZ))


@pytest.mark.parametrize(
    ("starts_at", "expected"),
    [
        (_local(2, 18, 30), True),  # today 18:30 local
        (_local(2, 12, 30), True),  # today, just after now
        (_local(2, 13, 0), True),
        (_local(3, 9, 0), False),  # tomorrow 09:00 local
        (_local(3, 0, 30), False),  # 00:30 local tomorrow is still today in UTC
        (_local(2, 0, 0), True),  # midnight local today is still today
        (_local(1, 18, 30), False),  # yesterday
    ],
)
def test_is_same_day_uses_the_clinic_calendar_day(starts_at: datetime, expected: bool) -> None:
    assert is_same_day(starts_at, NOW, TZ) is expected


def test_the_clinic_day_ends_at_local_midnight_not_utc_midnight() -> None:
    late = datetime(2026, 11, 2, 19, 0, tzinfo=UTC)  # 23:00 local, still Monday
    next_day = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)  # 00:00 local Tuesday

    assert is_same_day(late, NOW, TZ) is True
    assert is_same_day(next_day, NOW, TZ) is False


def test_requires_approval_is_the_same_day_rule() -> None:
    assert requires_approval(_local(2, 18, 30), NOW, TZ) is True
    assert requires_approval(_local(3, 9, 0), NOW, TZ) is False


@pytest.mark.parametrize(
    ("starts_at", "expected"),
    [
        (_local(2, 11, 59), True),
        (_local(2, 12, 0), True),  # starting right now has started
        (_local(2, 12, 1), False),
    ],
)
def test_a_slot_that_starts_now_or_earlier_has_started(starts_at: datetime, expected: bool) -> None:
    assert has_started(starts_at, NOW) is expected
