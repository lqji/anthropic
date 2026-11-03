from datetime import date

import pytest

from clinic.clock import FixedClock
from clinic.domain.dates import resolve_date, today_in

MONDAY = date(2026, 11, 2)


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Thursday", date(2026, 11, 5)),
        ("is she free on thursday?", date(2026, 11, 5)),
        ("Monday", MONDAY),
        ("Sunday", date(2026, 11, 8)),
        ("today", MONDAY),
        ("tomorrow morning", date(2026, 11, 3)),
        ("on 2026-12-24 please", date(2026, 12, 24)),
        ("2026-02-30", None),
        ("whenever", None),
        ("", None),
    ],
)
def test_resolve_date(phrase: str, expected: date | None) -> None:
    assert resolve_date(phrase, MONDAY) == expected


def test_today_in_uses_the_clinic_time_zone(fixed_clock: FixedClock) -> None:
    assert today_in(fixed_clock, "Asia/Muscat") == MONDAY
    late = FixedClock(fixed_clock.now().replace(hour=21))  # 01:00 next day in Muscat
    assert today_in(late, "Asia/Muscat") == date(2026, 11, 3)
    assert today_in(late, "UTC") == MONDAY
