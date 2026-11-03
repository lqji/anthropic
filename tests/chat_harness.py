"""A chat client over the real app for end-to-end tests (CFD-14 and CFD-15): start conversations,
say things, book directly in the database, decide approvals, and see where the graph is paused."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinic.app import create_app
from clinic.clock import FixedClock
from clinic.config import Settings

# Monday 2026-11-02, 12:00 at the clinic (Asia/Muscat, UTC+4): "tomorrow" is 2026-11-03.
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
TOMORROW = date(2026, 11, 3)
PATIENT = "demo-patient-1"
DECISION = {"decided_by": "desk-1", "reason": "Patient called to confirm"}


class Chat:
    def __init__(self, client: TestClient, url: str) -> None:
        self.client = client
        self.url = url

    def start(self, patient_ref: str = PATIENT) -> str:
        response = self.client.post("/api/v1/conversations", json={"patient_ref": patient_ref})
        assert response.status_code == 201
        return str(response.json()["id"])

    def say(self, conversation_id: str, text: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages", json={"text": text}
        )
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        detail = self.client.get(f"/api/v1/conversations/{conversation_id}").json()
        messages: list[dict[str, Any]] = detail["messages"]
        return messages

    def pending(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = self.client.get("/api/v1/approvals").json()
        return pending

    def decide(self, approval_id: str, verb: str = "approve") -> Any:
        return self.client.post(f"/api/v1/approvals/{approval_id}/{verb}", json=DECISION)

    def book(self, conversation_id: str, day: date = TOMORROW, nth: int = 0) -> tuple[int, int]:
        """A confirmed booking of Dr. Salma's `nth` slot that day for this conversation's
        patient. Returns (booking id, slot id)."""
        with psycopg.connect(self.url, autocommit=True) as conn:
            slot = conn.execute(
                """
                SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id
                WHERE d.name = 'Dr. Salma Nasser'
                  AND (s.starts_at AT TIME ZONE 'Asia/Muscat')::date = %s
                ORDER BY s.starts_at OFFSET %s LIMIT 1
                """,
                (day, nth),
            ).fetchone()
            assert slot is not None
            conn.execute("UPDATE slots SET status = 'booked' WHERE id = %s", (slot[0],))
            booking = conn.execute(
                """
                INSERT INTO bookings (slot_id, conversation_id, status, created_at, updated_at)
                VALUES (%s, %s, 'confirmed', %s, %s) RETURNING id
                """,
                (slot[0], UUID(conversation_id), NOW, NOW),
            ).fetchone()
            assert booking is not None
            return booking[0], slot[0]

    def status_of(self, table: str, row_id: int) -> str:
        with psycopg.connect(self.url, autocommit=True) as conn:
            row = conn.execute(
                f"SELECT status FROM {table} WHERE id = %s",  # noqa: S608 - fixed table names
                (row_id,),
            ).fetchone()
        assert row is not None
        return str(row[0])

    def paused_at(self, conversation_id: str) -> tuple[str, ...]:
        app = self.client.app
        assert isinstance(app, FastAPI)
        graph: Any = app.state.deps.graph
        config = {"configurable": {"thread_id": conversation_id}}
        return tuple(graph.get_state(config).next)


@contextmanager
def running(url: str) -> Iterator[Chat]:
    """One api process on this database. Leaving the block stops it (a restart)."""
    settings = Settings(llm_provider="fake", database_url=url)
    with TestClient(create_app(settings, clock=FixedClock(NOW))) as client:
        yield Chat(client, url)
