"""When a booking needs front-desk approval (CFD-15). Pure functions, no I/O.

The clinic's calendar day decides, not UTC: a slot at 00:30 clinic time tomorrow is a later day
even though it is still today in UTC.
"""

from datetime import datetime

from clinic.domain.schedule import local_date


def is_same_day(starts_at: datetime, now: datetime, timezone: str) -> bool:
    """True if the slot starts on the same clinic calendar day as `now`."""
    return local_date(starts_at, timezone) == local_date(now, timezone)


def has_started(starts_at: datetime, now: datetime) -> bool:
    """True if the slot starts now or earlier: it is never offered or bookable."""
    return starts_at <= now


def requires_approval(starts_at: datetime, now: datetime, timezone: str) -> bool:
    """A same-day booking waits for staff approval while the slot is held; later days do not."""
    return is_same_day(starts_at, now, timezone)
