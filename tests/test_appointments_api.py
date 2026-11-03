"""Doctors, specialties and free slots over HTTP, on a freshly seeded real PostgreSQL."""

from collections.abc import Iterator
from datetime import UTC, datetime, time
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.clock import FixedClock
from clinic.config import Settings
from tests.conftest import temporary_database

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4).
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
TODAY, TOMORROW, YESTERDAY = "2026-11-02", "2026-11-03", "2026-11-01"
DAY_STARTS = [time(h, m) for h in (9, 10, 11, 12, 14, 15, 16, 17, 18) for m in (0, 30)]


def _client(database_url: str) -> TestClient:
    settings = Settings(llm_provider="fake", database_url=database_url)
    return TestClient(create_app(settings, clock=FixedClock(NOW)))


@pytest.fixture(scope="module")
def client(database_url: str) -> Iterator[TestClient]:
    """One seeded database for the read-only tests of this module."""
    with temporary_database(database_url) as url, _client(url) as test_client:
        yield test_client


def _slots(client: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = client.get("/api/v1/slots", params=params)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _local_times(slots: list[dict[str, Any]]) -> list[time]:
    return [datetime.fromisoformat(slot["starts_at"]).time() for slot in slots]


# --- Doctors and specialties ----------------------------------------------------------------


def test_doctors_of_a_specialty(client: TestClient) -> None:
    response = client.get("/api/v1/doctors", params={"specialty": "Dermatology"})

    assert response.status_code == 200
    assert [d["name"] for d in response.json()] == ["Dr. Salma Nasser"]
    assert response.json()[0] == {"id": 1, "name": "Dr. Salma Nasser", "specialty": "Dermatology"}


def test_doctors_filter_ignores_case_and_lists_all_without_it(client: TestClient) -> None:
    lower = client.get("/api/v1/doctors", params={"specialty": "dermatology"}).json()
    every = client.get("/api/v1/doctors").json()

    assert [d["name"] for d in lower] == ["Dr. Salma Nasser"]
    assert [d["name"] for d in every] == ["Dr. Khalid Rahman", "Dr. Salma Nasser"]


def test_doctors_of_an_unknown_specialty_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/doctors", params={"specialty": "Cardiology"})
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_specialties_lists_two(client: TestClient) -> None:
    response = client.get("/api/v1/specialties")

    assert response.status_code == 200
    assert [s["name"] for s in response.json()] == ["Dermatology", "General Practice"]


# --- Free slots -----------------------------------------------------------------------------


def test_slots_of_a_doctor_tomorrow_are_free_ordered_and_in_clinic_time(
    client: TestClient,
) -> None:
    slots = _slots(client, doctor_id=1, date=TOMORROW)

    assert {s["doctor_id"] for s in slots} == {1}
    assert {s["doctor"] for s in slots} == {"Dr. Salma Nasser"}
    assert _local_times(slots) == DAY_STARTS  # 18 slots, none in the 13:00-14:00 lunch break
    assert all(s["starts_at"].startswith(TOMORROW) for s in slots)
    assert all(s["starts_at"].endswith("+04:00") for s in slots)
    starts = [datetime.fromisoformat(s["starts_at"]) for s in slots]
    assert starts == sorted(starts)


def test_slots_today_leave_out_slots_that_already_started(client: TestClient) -> None:
    slots = _slots(client, doctor_id=1, date=TODAY)  # it is 12:00 at the clinic

    assert _local_times(slots) == [t for t in DAY_STARTS if t > time(12, 0)]
    assert _local_times(slots)[0] == time(12, 30)


def test_slots_by_specialty(client: TestClient) -> None:
    slots = _slots(client, specialty="General Practice", date=TOMORROW)

    assert {s["doctor"] for s in slots} == {"Dr. Khalid Rahman"}
    assert len(slots) == 18


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({"date": TOMORROW}, 422, "validation_error"),  # no doctor_id or specialty
        ({"doctor_id": 1}, 422, "validation_error"),  # no date
        ({"doctor_id": 1, "date": "2026-02-30"}, 422, "validation_error"),  # not a date
        ({"doctor_id": 1, "date": "tomorrow"}, 422, "validation_error"),
        ({"doctor_id": 1, "date": YESTERDAY}, 422, "validation_error"),  # in the past
        ({"doctor_id": 999, "date": TOMORROW}, 404, "not_found"),
        ({"specialty": "Cardiology", "date": TOMORROW}, 404, "not_found"),
    ],
)
def test_slots_rejects_bad_requests(
    client: TestClient, params: dict[str, Any], status: int, code: str
) -> None:
    response = client.get("/api/v1/slots", params=params)

    assert response.status_code == status
    assert response.json()["code"] == code


def test_booked_and_held_slots_are_not_listed(database_url: str) -> None:
    with temporary_database(database_url) as url, _client(url) as client:
        first, second, *_ = _slots(client, doctor_id=1, date=TOMORROW)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (first["id"],))
            conn.execute("UPDATE slots SET status = 'held' WHERE id = %s", (second["id"],))

        slots = _slots(client, doctor_id=1, date=TOMORROW)

    assert len(slots) == 16
    assert {first["id"], second["id"]}.isdisjoint(s["id"] for s in slots)
