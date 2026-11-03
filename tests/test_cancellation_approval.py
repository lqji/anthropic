import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clinic.config import Settings
from clinic.repositories import approvals
from clinic.workflows.approval import ApprovalState
from tests.approval_harness import FakeBookings, running_app
from tests.test_conversation_api import SEED_DIR

CANCEL = "Cancel my appointment tomorrow"
LABEL = "on 2026-11-04 at 09:30 with Dr. Salma Nasser"
DECISION = {"decided_by": "desk-1", "reason": "Patient called to confirm"}


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Settings:
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("TRUNCATE conversations CASCADE")  # cascades to messages and approvals
    return make_settings(database_url=database_url, clinic_docs_dir=SEED_DIR)


@pytest.fixture
def bookings() -> FakeBookings:
    fake = FakeBookings()
    fake.add(7, LABEL)
    return fake


def _start(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"patient_ref": "demo-patient-1"})
    assert response.status_code == 201
    return str(response.json()["id"])


def _say(client: TestClient, conversation_id: str, text: str) -> dict[str, str]:
    response = client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    body: dict[str, str] = response.json()
    return body


def _messages(client: TestClient, conversation_id: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = client.get(f"/api/v1/conversations/{conversation_id}").json()[
        "messages"
    ]
    return messages


def _request_cancellation(client: TestClient) -> tuple[str, str]:
    conversation_id = _start(client)
    body = _say(client, conversation_id, CANCEL)
    return conversation_id, body["approval_id"]


def _pending(client: TestClient) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = client.get("/api/v1/approvals").json()
    return pending


# --- AC1: cancellation pauses the graph with a pending approval -----------------------------


def test_cancel_request_pauses_at_await_approval_with_pending_approval(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, harness):
        conversation_id = _start(client)
        body = _say(client, conversation_id, CANCEL)

        assert body["status"] == "awaiting_approval"
        assert UUID(body["approval_id"])
        [approval] = _pending(client)
        assert approval["id"] == body["approval_id"]
        assert approval["kind"] == "cancellation"
        assert approval["booking_id"] == 7
        assert harness.paused_at(conversation_id) == ("await_approval",)  # real interrupt()
        assert bookings.status(7) == "confirmed"
        detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert detail["status"] == "awaiting_approval"


# --- AC2: the pause survives a restart --------------------------------------------------------


def test_pending_approval_survives_restart_and_approving_resumes(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        conversation_id, approval_id = _request_cancellation(client)

    # A new process: new checkpointer connection, new graph, new pool, new app.
    with running_app(settings, bookings) as (client, harness):
        assert [a["id"] for a in _pending(client)] == [approval_id]
        assert harness.paused_at(conversation_id) == ("await_approval",)

        response = client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

        assert response.status_code == 200
        assert bookings.status(7) == "cancelled"
        assert harness.paused_at(conversation_id) == ()


# --- AC3: approve / reject ------------------------------------------------------------------


def test_approve_cancels_booking_and_tells_the_patient(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        conversation_id, approval_id = _request_cancellation(client)

        response = client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["patient_message"] == f"Your appointment {LABEL} has been cancelled."
        assert bookings.cancel_calls == [7]
        assert _messages(client, conversation_id)[-1]["text"] == body["patient_message"]
        detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
        assert detail["status"] == "active"
        assert _pending(client) == []


def test_reject_keeps_booking_and_gives_the_reason(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        conversation_id, approval_id = _request_cancellation(client)

        response = client.post(
            f"/api/v1/approvals/{approval_id}/reject",
            json={"decided_by": "desk-1", "reason": "Within 2 hours of the visit"},
        )

        assert response.status_code == 200
        message = response.json()["patient_message"]
        assert "Within 2 hours of the visit" in message
        assert "still booked" in message
        assert bookings.status(7) == "confirmed"
        assert bookings.cancel_calls == []
        assert _messages(client, conversation_id)[-1]["text"] == message


# --- AC4: invalid and repeated decisions ----------------------------------------------------


def test_second_decision_is_409_already_decided(settings: Settings, bookings: FakeBookings) -> None:
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)
        client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

        response = client.post(f"/api/v1/approvals/{approval_id}/reject", json=DECISION)

        assert response.status_code == 409
        assert response.json()["code"] == "already_decided"
        assert bookings.cancel_calls == [7]


@pytest.mark.parametrize("approval_id", [str(uuid4()), "not-a-uuid"])
def test_unknown_approval_is_404(
    settings: Settings, bookings: FakeBookings, approval_id: str
) -> None:
    with running_app(settings, bookings) as (client, _):
        response = client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


@pytest.mark.parametrize(
    "body",
    [
        {"decided_by": "desk-1", "reason": "ok"},
        {"decided_by": "desk-1", "reason": "   "},
        {"decided_by": "   ", "reason": "Patient called"},
        {"reason": "Patient called"},
        {"decided_by": "desk-1"},
    ],
)
def test_invalid_decision_is_422_and_stays_pending(
    settings: Settings, bookings: FakeBookings, body: dict[str, str]
) -> None:
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)

        response = client.post(f"/api/v1/approvals/{approval_id}/approve", json=body)

        assert response.status_code == 422
        assert [a["id"] for a in _pending(client)] == [approval_id]


def test_two_concurrent_decisions_one_200_one_409(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)
        bookings.gate = threading.Event()  # the first decision pauses inside apply_decision

        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(
                client.post, f"/api/v1/approvals/{approval_id}/approve", json=DECISION
            )
            assert bookings.entered.wait(5)
            second = client.post(f"/api/v1/approvals/{approval_id}/reject", json=DECISION)
            bookings.gate.set()
            first_response = first.result(timeout=10)

    assert first_response.status_code == 200
    assert second.status_code == 409
    assert bookings.cancel_calls == [7]


def test_conditional_update_lets_only_one_decision_win(
    settings: Settings, bookings: FakeBookings
) -> None:
    """Across processes there is no shared lock: the conditional UPDATE decides."""
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)

    now = datetime.now(UTC)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        first = approvals.decide(conn, UUID(approval_id), "approved", "desk-1", "fine", now)
        second = approvals.decide(conn, UUID(approval_id), "rejected", "desk-2", "no", now)

    assert first is not None and first.status == "approved"
    assert second is None


# --- AC5: conversation busy -----------------------------------------------------------------


def test_decision_while_conversation_is_busy_is_409_and_stays_pending(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        conversation_id, approval_id = _request_cancellation(client)

        app = client.app
        assert isinstance(app, FastAPI)
        with app.state.deps.locks.hold(conversation_id):  # another request is mid-turn
            response = client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)

        assert response.status_code == 409
        assert response.json()["code"] == "conversation_busy"
        assert [a["id"] for a in _pending(client)] == [approval_id]
        assert bookings.cancel_calls == []


# --- AC6: paused conversation ---------------------------------------------------------------


def test_paused_conversation_says_still_waiting_without_invoking_the_graph(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, harness):
        conversation_id, _ = _request_cancellation(client)
        calls_before = harness.route_calls

        body = _say(client, conversation_id, "hello, any news?")

        assert body["outcome"] == "still_waiting"
        assert body["status"] == "awaiting_approval"
        assert harness.route_calls == calls_before
        assert harness.paused_at(conversation_id) == ("await_approval",)


def test_paused_conversation_still_answers_and_flags_emergencies(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, harness):
        conversation_id, approval_id = _request_cancellation(client)
        calls_before = harness.route_calls

        body = _say(client, conversation_id, "I have crushing chest pain")

        assert body["outcome"] == "emergency"
        assert harness.route_calls == calls_before
        [flag] = client.get("/api/v1/emergencies").json()
        assert flag["conversation_id"] == conversation_id
        assert [a["id"] for a in _pending(client)] == [approval_id]


# --- AC7: no match, two matches, already pending --------------------------------------------


def test_no_matching_booking_is_cancel_not_found(settings: Settings) -> None:
    with running_app(settings, FakeBookings()) as (client, _):
        conversation_id = _start(client)
        body = _say(client, conversation_id, CANCEL)

        assert body["outcome"] == "cancel_not_found"
        assert body["status"] == "active"
        assert "approval_id" not in body
        assert _pending(client) == []


def test_two_matching_bookings_asks_which_one(settings: Settings) -> None:
    two = FakeBookings()
    two.add(7, LABEL)
    two.add(8, "on 2026-11-04 at 15:00 with Dr. Omar Said")
    with running_app(settings, two) as (client, _):
        body = _say(client, _start(client), CANCEL)

        assert body["outcome"] == "cancel_ambiguous"
        assert LABEL in body["reply"] and "15:00" in body["reply"]
        assert body["reply"].endswith("Which one would you like to cancel?")
        assert _pending(client) == []


def test_cancellation_already_pending_says_already_waiting(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)
        other_conversation = _start(client)

        body = _say(client, other_conversation, CANCEL)

        assert body["outcome"] == "already_pending"
        assert "already waiting for staff approval" in body["reply"]
        assert body["status"] == "active"
        assert [a["id"] for a in _pending(client)] == [approval_id]


# --- AC8: startup reconcile -----------------------------------------------------------------


def test_decision_stored_but_not_resumed_is_resumed_at_startup(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        conversation_id, approval_id = _request_cancellation(client)

    # The process died right after committing the decision, before resuming the graph.
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        decided = approvals.decide(
            conn, UUID(approval_id), "approved", "desk-1", "Patient called", datetime.now(UTC)
        )
    assert decided is not None and decided.resumed_at is None

    with running_app(settings, bookings) as (client, harness):  # startup runs the reconcile
        assert bookings.status(7) == "cancelled"
        assert harness.paused_at(conversation_id) == ()
        assert _messages(client, conversation_id)[-1]["text"].endswith("has been cancelled.")

    with psycopg.connect(settings.database_url) as conn:
        row = approvals.get(conn, UUID(approval_id))
    assert row is not None and row.resumed_at is not None


def test_reconcile_after_the_graph_already_finished_does_not_apply_twice(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, _):
        _, approval_id = _request_cancellation(client)
        client.post(f"/api/v1/approvals/{approval_id}/approve", json=DECISION)
    # The graph finished, but the process died before resumed_at was stored.
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute("UPDATE approvals SET resumed_at = NULL WHERE id = %s", (UUID(approval_id),))

    with running_app(settings, bookings):
        pass

    assert bookings.cancel_calls == [7]
    with psycopg.connect(settings.database_url) as conn:
        row = approvals.get(conn, UUID(approval_id))
    assert row is not None and row.resumed_at is not None


def test_reconcile_logs_a_failing_approval_and_still_resumes_the_others(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    two = FakeBookings()
    two.add(7, LABEL)
    two.add(8, LABEL, patient="demo-patient-2")
    with running_app(settings, two) as (client, harness):
        first, first_approval = _request_cancellation(client)
        second = _start(client)
        with psycopg.connect(settings.database_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE conversations SET patient_ref = 'demo-patient-2' WHERE id = %s",
                (UUID(second),),
            )
        harness.patient_ref = "demo-patient-2"
        second_approval = _say(client, second, CANCEL)["approval_id"]

    now = datetime.now(UTC)
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        for approval_id in (first_approval, second_approval):
            approvals.decide(conn, UUID(approval_id), "approved", "desk-1", "fine", now)
        # Break the first: its checkpoint now waits for a different approval.
        conn.execute("UPDATE approvals SET id = %s WHERE id = %s", (uuid4(), UUID(first_approval)))

    with caplog.at_level(logging.ERROR, logger="clinic.approval"):
        with running_app(settings, two) as (_, harness):
            assert harness.paused_at(second) == ()
            assert harness.paused_at(first) == ("await_approval",)  # left for staff to look at

    assert two.status(8) == "cancelled"
    assert two.status(7) == "confirmed"
    assert [r.getMessage() for r in caplog.records if r.name == "clinic.approval"] == [
        "approval.reconcile_failed"
    ]


def test_two_conversations_racing_for_one_booking_get_one_approval(
    settings: Settings, bookings: FakeBookings
) -> None:
    with running_app(settings, bookings) as (client, harness):
        first, second = _start(client), _start(client)
        # Both passed the "already pending?" check before either created its approval.
        won, lost = (
            harness.nodes.request_approval(
                ApprovalState(
                    conversation_id=conversation_id,
                    patient_ref="demo-patient-1",
                    approval_kind="cancellation",
                    booking_id=7,
                    booking_label=LABEL,
                )
            )
            for conversation_id in (first, second)
        )

    assert won["approval_id"] is not None
    assert lost["approval_id"] is None
    assert lost["outcome"] == "already_pending"
    assert len(_pending_rows(settings)) == 1


def _pending_rows(settings: Settings) -> list[approvals.ApprovalRow]:
    with psycopg.connect(settings.database_url) as conn:
        return approvals.list_by_status(conn, "pending")
