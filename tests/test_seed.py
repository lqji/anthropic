"""The demo catalogue and slots inserted on first start."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.domain.schedule import day_bounds, local_date, slot_starts
from clinic.schema import apply_schema
from clinic.seed import SEED_DAYS, seed_appointments, seed_slots
from tests.conftest import temporary_database

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
FIRST_DAY = date(2026, 11, 2)
DOCTOR_IDS = [1, 2]


def _env_example(name: str) -> str:
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].split("#", 1)[0].strip()
    raise AssertionError(f"{name} is not in .env.example")


# --- Unit: pure schedule and seed data ------------------------------------------------------


def test_seed_time_zone_is_the_one_in_env_example() -> None:
    timezone = _env_example("CLINIC_TIMEZONE")

    assert timezone == "Asia/Muscat"
    assert Settings.model_fields["clinic_timezone"].default == timezone
    # Seeded with that zone, the first slot is 09:00 at the clinic, which is 05:00 UTC in Muscat.
    first_start = seed_slots(DOCTOR_IDS, FIRST_DAY, timezone)[0][1]
    assert first_start.astimezone(ZoneInfo(timezone)).time() == time(9, 0)
    assert first_start.astimezone(UTC).time() == time(5, 0)


def test_seed_has_180_thirty_minute_slots_18_per_doctor_per_day() -> None:
    timezone = _env_example("CLINIC_TIMEZONE")
    slots = seed_slots(DOCTOR_IDS, FIRST_DAY, timezone)

    per_doctor_day = Counter((doctor, local_date(start, timezone)) for doctor, start, _ in slots)
    assert len(slots) == 180
    assert set(per_doctor_day.values()) == {18}
    assert {day for _, day in per_doctor_day} == {
        FIRST_DAY + timedelta(days=n) for n in range(SEED_DAYS)
    }
    assert all(end - start == timedelta(minutes=30) for _, start, end in slots)


def test_a_day_runs_09_to_13_and_14_to_19() -> None:
    starts = [s.time() for s in slot_starts(FIRST_DAY, "Asia/Muscat")]

    assert starts[0] == time(9, 0)
    assert starts[-1] == time(18, 30)  # last slot ends at 19:00
    assert time(13, 0) not in starts and time(13, 30) not in starts
    assert len(starts) == 18


def test_day_bounds_are_clinic_midnights() -> None:
    start, end = day_bounds(FIRST_DAY, "Asia/Muscat")

    assert start == datetime(2026, 11, 1, 20, 0, tzinfo=UTC)
    assert end - start == timedelta(days=1)


# --- Integration: first start seeds once ----------------------------------------------------


def test_first_start_seeds_and_a_restart_adds_nothing(database_url: str) -> None:
    clock = FixedClock(datetime(2026, 11, 2, 8, 0, tzinfo=UTC))  # Monday 12:00 at the clinic
    with temporary_database(database_url) as url:
        settings = Settings(llm_provider="fake", database_url=url)
        for _ in range(2):
            with TestClient(create_app(settings, clock=clock)):
                pass
            clock.set(clock.now() + timedelta(days=1))  # a later start must not add slots

        with psycopg.connect(url) as conn:
            counts = conn.execute(
                """
                SELECT (SELECT count(*) FROM specialties), (SELECT count(*) FROM doctors),
                       (SELECT count(*) FROM slots), (SELECT count(*) FROM slots
                                                     WHERE status = 'free')
                """
            ).fetchone()
            per_doctor_day = conn.execute(
                """
                SELECT DISTINCT count(*) FROM slots
                GROUP BY doctor_id, (starts_at AT TIME ZONE 'Asia/Muscat')::date
                """
            ).fetchall()
            first_day = conn.execute(
                "SELECT min((starts_at AT TIME ZONE 'Asia/Muscat')::date) FROM slots"
            ).fetchone()

    assert counts == (2, 2, 180, 180)
    assert per_doctor_day == [(18,)]
    assert first_day == (FIRST_DAY,)  # seeded from today, so same-day slots exist


def test_a_seed_folder_without_doctors_stops_startup_and_seeds_nothing(
    database_url: str, tmp_path: Path
) -> None:
    (tmp_path / "seed").mkdir()  # exists, holds no .sql files
    with temporary_database(database_url) as url, psycopg.connect(url, autocommit=True) as conn:
        apply_schema(conn)
        with pytest.raises(ValueError, match="added no doctors"):
            seed_appointments(
                conn, tmp_path, "Asia/Muscat", datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
            )
        row = conn.execute("SELECT (SELECT count(*) FROM doctors), (SELECT count(*) FROM slots)")
        assert row.fetchone() == (0, 0)
