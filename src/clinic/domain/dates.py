import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from clinic.clock import Clock

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_WORD = re.compile(r"[a-z]+")


def today_in(clock: Clock, timezone: str) -> date:
    """The calendar date it is right now at the clinic."""
    return clock.now().astimezone(ZoneInfo(timezone)).date()


def resolve_date(phrase: str, today: date) -> date | None:
    """Find the date a phrase refers to, or None.

    Understands ISO dates, "today", "tomorrow" and weekday names. A weekday means its next
    occurrence, and today when it is that weekday.
    """
    iso = _ISO_DATE.search(phrase)
    if iso:
        try:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        except ValueError:
            return None
    for word in _WORD.findall(phrase.lower()):
        if word == "today":
            return today
        if word == "tomorrow":
            return today + timedelta(days=1)
        if word in _WEEKDAYS:
            return today + timedelta(days=(_WEEKDAYS[word] - today.weekday()) % 7)
    return None
