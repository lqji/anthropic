"""A small conversation graph with the real CFD-14 approval nodes and a real PostgresSaver.

Stand-ins for what other stories provide: `route` plays CFD-05's graph routing plus CFD-07's
intent and date extraction, and `FakeBookings` plays CFD-12/13's bookings and slots.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from psycopg_pool import ConnectionPool

from clinic.app import create_app
from clinic.clock import SystemClock
from clinic.config import Settings
from clinic.services.turn import TurnResult
from clinic.workflows.approval import (
    ApprovalNodes,
    ApprovalState,
    BookingRef,
    GraphApprovalResumer,
    route_after_cancel,
    route_after_request,
)

PATIENT = "demo-patient-1"
TOMORROW = "2026-11-04"


def seed_bookings(conn: psycopg.Connection[Any], booking_ids: list[int]) -> None:
    """Real `confirmed` bookings with these ids. An approval references a real booking (foreign
    key), while the bookings the nodes see in these tests come from `FakeBookings`."""
    conn.execute("INSERT INTO specialties (id, name) VALUES (9001, 'Test') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO doctors (id, name, specialty_id) VALUES (9001, 'Dr. Test', 9001)"
        " ON CONFLICT DO NOTHING"
    )
    first = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    for n, booking_id in enumerate(booking_ids):
        starts = first + timedelta(minutes=30 * n)
        slot = conn.execute(
            """
            INSERT INTO slots (doctor_id, starts_at, ends_at, status)
            VALUES (9001, %(starts)s, %(ends)s, 'booked')
            ON CONFLICT (doctor_id, starts_at) DO UPDATE SET status = 'booked'
            RETURNING id
            """,
            {"starts": starts, "ends": starts + timedelta(minutes=30)},
        ).fetchone()
        assert slot is not None
        conversation_id = uuid4()
        conn.execute(
            "INSERT INTO conversations (id, patient_ref, created_at) VALUES (%s, 'seed', %s)",
            (conversation_id, first),
        )
        conn.execute(
            """
            INSERT INTO bookings (id, slot_id, conversation_id, status, created_at, updated_at)
            OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, 'confirmed', %s, %s)
            """,
            (booking_id, slot[0], conversation_id, first, first),
        )


class HarnessState(ApprovalState, total=False):
    text: str
    intent: str | None


@dataclass
class FakeBooking:
    patient_ref: str
    on: date
    label: str
    status: str = "confirmed"


@dataclass
class FakeBookings:
    """Implements BookingPort. `gate` pauses `cancel` (to hold the conversation lock);
    `cancel_error` makes `cancel` fail."""

    bookings: dict[int, FakeBooking] = field(default_factory=dict)
    cancel_calls: list[int] = field(default_factory=list)
    entered: threading.Event = field(default_factory=threading.Event)
    gate: threading.Event | None = None
    cancel_error: Exception | None = None

    def add(self, booking_id: int, label: str, on: str = TOMORROW, patient: str = PATIENT) -> None:
        self.bookings[booking_id] = FakeBooking(patient, date.fromisoformat(on), label)

    def find_confirmed(self, patient_ref: str, on_date: date | None) -> list[BookingRef]:
        return [
            BookingRef(id=booking_id, label=booking.label)
            for booking_id, booking in sorted(self.bookings.items())
            if booking.patient_ref == patient_ref
            and booking.status == "confirmed"
            and (on_date is None or booking.on == on_date)
        ]

    def cancel(self, booking_id: int) -> None:
        self.cancel_calls.append(booking_id)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(10)
        if self.cancel_error is not None:
            raise self.cancel_error
        self.bookings[booking_id].status = "cancelled"

    def status(self, booking_id: int) -> str:
        return self.bookings[booking_id].status

    def confirm_held(self, booking_id: int) -> None:
        self.bookings[booking_id].status = "confirmed"

    def reject_held(self, booking_id: int) -> None:
        self.bookings[booking_id].status = "rejected"


class ProcessDied(RuntimeError):
    """Stands for the process being killed at an awkward moment."""


class Harness:
    def __init__(self, saver: PostgresSaver, pool: ConnectionPool, bookings: FakeBookings) -> None:
        self.bookings = bookings
        self.route_calls = 0
        self.target_date_override: str | None = None  # feeds the graph a malformed date
        # The process dies after request_approval saved its row but before LangGraph wrote the
        # checkpoint: the approval exists, but the graph never reached its pause.
        self.crash_after_request_approval = False
        self.patient_ref = (
            PATIENT  # who the next turn is from (CFD-05 reads it from the conversation)
        )
        self.nodes = nodes = ApprovalNodes(pool, SystemClock(), bookings)
        builder = StateGraph(HarnessState)
        builder.add_node("route", self.route)
        builder.add_node("cancel", nodes.cancel)
        builder.add_node("request_approval", self.request_approval)
        builder.add_node("await_approval", nodes.await_approval)
        builder.add_node("apply_decision", nodes.apply_decision)
        builder.add_edge(START, "route")
        builder.add_conditional_edges("route", self.after_route)
        builder.add_conditional_edges("cancel", route_after_cancel)
        builder.add_conditional_edges("request_approval", route_after_request)
        builder.add_edge("await_approval", "apply_decision")
        builder.add_edge("apply_decision", END)
        self.graph = builder.compile(checkpointer=saver)
        self.resumer = GraphApprovalResumer(self.graph)

    def request_approval(self, state: HarnessState) -> dict[str, object]:
        update = self.nodes.request_approval(state)
        if self.crash_after_request_approval:
            self.crash_after_request_approval = False
            raise ProcessDied("the process died after saving the approval")
        return update

    def route(self, state: HarnessState) -> dict[str, object]:
        """Stand-in for CFD-05 routing and CFD-07 intent/date extraction."""
        self.route_calls += 1
        text = state["text"].lower()
        if "cancel" in text:
            target = self.target_date_override or (TOMORROW if "tomorrow" in text else None)
            return {"intent": "cancel", "target_date": target}
        reply = f"echo: {state['text']}"
        return {"intent": "other", "reply": reply, "outcome": "answered", "status": "active"}

    @staticmethod
    def after_route(state: HarnessState) -> str:
        return "cancel" if state.get("intent") == "cancel" else END

    def run_turn(self, conversation_id: str, text: str) -> TurnResult:
        """Implements ConversationWorkflow over the graph, as CFD-05's adapter will."""
        turn_input: HarnessState = {
            "conversation_id": conversation_id,
            "patient_ref": self.patient_ref,
            "text": text,
            "intent": None,
            "target_date": None,
            "approval_id": None,
        }
        values: dict[str, Any] = self.graph.invoke(turn_input, self.config(conversation_id))
        return TurnResult(
            reply=values["reply"],
            intent=values.get("intent") or "other",
            outcome=values["outcome"],
            status=values["status"],
            approval_id=values.get("approval_id"),
        )

    def paused_at(self, conversation_id: str) -> tuple[str, ...]:
        return tuple(self.graph.get_state(self.config(conversation_id)).next)

    @staticmethod
    def config(conversation_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": conversation_id}}


@contextmanager
def running_app(settings: Settings, bookings: FakeBookings) -> Iterator[tuple[TestClient, Harness]]:
    """One api instance: its own checkpointer connection, node pool and app (a fresh process)."""
    with (
        PostgresSaver.from_conn_string(settings.database_url) as saver,
        ConnectionPool(settings.database_url, min_size=1, max_size=4, open=True) as pool,
    ):
        saver.setup()
        harness = Harness(saver, pool, bookings)
        app = create_app(settings, workflow=harness, approval_resumer=harness.resumer)
        with TestClient(app) as client:
            yield client, harness
