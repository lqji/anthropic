"""Approval nodes (CFD-14): cancel -> request_approval -> await_approval -> apply_decision.

CFD-05's conversation graph adds these nodes (its state includes the `ApprovalState` fields) and
routes the `cancel` intent from CFD-07 to `cancel`. `await_approval` is a real LangGraph
`interrupt()`; the pause is checkpointed by PostgresSaver and survives a restart. CFD-15 adds
the same-day booking case to `request_approval` and `apply_decision`.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, TypedDict
from uuid import UUID, uuid4

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command, StateSnapshot, interrupt
from psycopg_pool import ConnectionPool

from clinic.clock import Clock
from clinic.repositories import approvals, conversations

CANCELLATION = "cancellation"
APPROVED = "approved"
REJECTED = "rejected"

CANCEL_NOT_FOUND = (
    "I couldn't find a confirmed appointment to cancel{on_day}. Please check the date, or call "
    "the front desk."
)
ASK_WHICH = "You have {count} appointments{on_day}: {labels}. Which one would you like to cancel?"
ASK_DATE = "I couldn't understand the date. Which day is the appointment you want to cancel?"
ALREADY_WAITING = (
    "A cancellation of your appointment {label} is already waiting for staff approval. "
    "I'll message you here when they decide."
)
REQUESTED = {
    CANCELLATION: (
        "I've asked our front-desk staff to approve cancelling your appointment {label}. "
        "It stays booked until they decide, and I'll message you here."
    ),
}
CANCELLED = "Your appointment {label} has been cancelled."
CANCEL_REJECTED = (
    "Your request to cancel your appointment {label} was not approved: {reason}. "
    "Your appointment is still booked."
)


class ApprovalState(TypedDict, total=False):
    """Fields the approval nodes read and write. Primitives only (checkpointed as msgpack)."""

    conversation_id: str
    patient_ref: str
    target_date: str | None  # ISO date from CFD-07's entities, e.g. "tomorrow" -> "2026-11-04"
    approval_kind: str | None
    booking_id: int | None
    booking_label: str | None
    approval_id: str | None
    # The approval whose decision `apply_decision` applied. `approval_id` is cleared then, so
    # this is what tells a finished turn which approval it belongs to.
    decided_approval_id: str | None
    decision: str | None
    decision_reason: str | None
    reply: str
    outcome: str
    status: str


@dataclass(frozen=True)
class BookingRef:
    id: int
    label: str  # how the patient sees it, e.g. "on Wed 4 Nov at 09:30 with Dr. Salma Nasser"


class BookingPort(Protocol):
    """Bookings as the approval nodes need them. CFD-12/13's appointments repository
    implements this."""

    def find_confirmed(self, patient_ref: str, on_date: date | None) -> list[BookingRef]:
        """The patient's confirmed upcoming bookings, on `on_date` if given."""
        ...

    def cancel(self, booking_id: int) -> None:
        """Booking `cancelled` and its slot `free`. Must be idempotent: after a crash, a resume
        can apply the same decision twice."""
        ...


@dataclass(frozen=True)
class ApprovalOutcome:
    reply: str
    outcome: str


class ApprovalNodes:
    def __init__(self, pool: ConnectionPool, clock: Clock, bookings: BookingPort) -> None:
        self._pool = pool
        self._clock = clock
        self._bookings = bookings

    def cancel(self, state: ApprovalState) -> dict[str, object]:
        """Find the one booking to cancel, or answer without asking staff."""
        target = state.get("target_date")
        try:
            on_date = date.fromisoformat(target) if target else None
        except ValueError:
            # The date comes from the intent step (an LLM), so it is not trusted: ask the patient.
            return _answered(ASK_DATE, "cancel_needs_date")
        on_day = f" on {target}" if target else ""
        matches = self._bookings.find_confirmed(state["patient_ref"], on_date)
        if not matches:
            return _answered(CANCEL_NOT_FOUND.format(on_day=on_day), "cancel_not_found")
        if len(matches) > 1:
            labels = "; ".join(booking.label for booking in matches)
            reply = ASK_WHICH.format(count=len(matches), on_day=on_day, labels=labels)
            return _answered(reply, "cancel_ambiguous")
        booking = matches[0]
        with self._pool.connection() as conn:
            pending = approvals.pending_for_booking(conn, CANCELLATION, booking.id)
        if pending is not None:
            return _answered(ALREADY_WAITING.format(label=booking.label), "already_pending")
        return {
            "approval_kind": CANCELLATION,
            "booking_id": booking.id,
            "booking_label": booking.label,
        }

    def request_approval(self, state: ApprovalState) -> dict[str, object]:
        """Create the pending approval and mark the conversation as waiting for it."""
        kind = state.get("approval_kind")
        booking_id = state.get("booking_id")
        if kind is None or booking_id is None:
            raise ValueError("request_approval needs approval_kind and booking_id")
        approval_id = uuid4()
        conversation_id = UUID(state["conversation_id"])
        label = state.get("booking_label") or ""
        try:
            with self._pool.connection() as conn:
                open_approval = approvals.open_for(conn, conversation_id, kind, booking_id)
                if open_approval is None:
                    approvals.create(
                        conn, approval_id, conversation_id, kind, booking_id, self._clock.now()
                    )
                else:
                    # An earlier run of this node saved the approval, then the process died before
                    # the checkpoint. Reuse it: a second approval would ask staff twice.
                    approval_id = open_approval.id
                conversations.set_status(conn, conversation_id, "awaiting_approval")
        except psycopg.errors.UniqueViolation:
            # Another conversation asked for the same booking a moment ago.
            return {
                **_answered(ALREADY_WAITING.format(label=label), "already_pending"),
                "approval_id": None,
            }
        return {
            "approval_id": str(approval_id),
            "decision": None,
            "decision_reason": None,
            "reply": REQUESTED[kind].format(label=label),
            "outcome": "awaiting_approval",
            "status": "awaiting_approval",
        }

    def await_approval(self, state: ApprovalState) -> dict[str, object]:
        """Pause until staff decide. Re-runs from the top on resume, so no side effects here."""
        answer = interrupt({"approval_id": state.get("approval_id")})
        return {"decision": answer["decision"], "decision_reason": answer["reason"]}

    def apply_decision(self, state: ApprovalState) -> dict[str, object]:
        """Carry out the staff decision. Generic over approval kinds."""
        kind = state.get("approval_kind")
        decision = state.get("decision")
        label = state.get("booking_label") or ""
        booking_id = state.get("booking_id")
        if decision not in (APPROVED, REJECTED):
            # Never read an unknown value as a rejection (or an approval).
            raise ValueError(f"unknown decision {decision!r}")
        if kind == CANCELLATION and booking_id is not None:
            if decision == APPROVED:
                self._bookings.cancel(booking_id)
                reply, outcome = CANCELLED.format(label=label), "cancelled"
            else:
                reason = state.get("decision_reason") or ""
                reply = CANCEL_REJECTED.format(label=label, reason=reason)
                outcome = "cancel_rejected"
        else:
            raise ValueError(f"no decision handling for approval kind {kind!r}")
        return {
            "reply": reply,
            "outcome": outcome,
            "status": "active",
            "approval_kind": None,
            "decided_approval_id": state.get("approval_id"),
            "approval_id": None,
            "booking_id": None,
            "booking_label": None,
        }


def route_after_cancel(state: ApprovalState) -> str:
    """To request_approval when cancel found exactly one booking, else the turn is done."""
    return "request_approval" if state.get("booking_id") is not None else END


def route_after_request(state: ApprovalState) -> str:
    """To await_approval when an approval was created, else (lost a race) the turn is done."""
    return "await_approval" if state.get("approval_id") is not None else END


def _answered(reply: str, outcome: str) -> dict[str, object]:
    return {
        "reply": reply,
        "outcome": outcome,
        "status": "active",
        "approval_kind": None,
        "booking_id": None,
        "booking_label": None,
    }


class CheckpointedGraph(Protocol):
    """The parts of a compiled LangGraph graph (with a checkpointer) the resumer uses."""

    def get_state(self, config: RunnableConfig) -> StateSnapshot: ...

    def invoke(self, input: Any, config: RunnableConfig | None = None) -> Any: ...


class GraphApprovalResumer:
    """Resumes the conversation graph paused at `await_approval` with Command(resume=...).

    Safe to call again for the same approval: it looks at where the saved checkpoint stands, so a
    decision is never applied twice, a half-finished resume is completed, and a graph that never
    reached its pause is brought there first.
    """

    MAX_STEPS = 3  # advance to the pause, hand over the decision, read the result

    def __init__(self, graph: CheckpointedGraph) -> None:
        self._graph = graph

    def resume(
        self, conversation_id: str, approval_id: str, decision: str, reason: str
    ) -> ApprovalOutcome:
        config: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
        for _ in range(self.MAX_STEPS):
            snapshot = self._graph.get_state(config)
            waiting = snapshot.next
            if "await_approval" in waiting:
                # Paused: hand the decision to the graph.
                _same_approval(snapshot.values.get("approval_id"), approval_id)
                values = self._graph.invoke(
                    Command(resume={"decision": decision, "reason": reason}), config
                )
                return _outcome(values)
            if "apply_decision" in waiting:
                # The decision was handed over, but `apply_decision` failed (for example it could
                # not cancel the booking). The checkpoint is saved just before that node, so
                # invoking with no input runs it again.
                _same_approval(snapshot.values.get("approval_id"), approval_id)
                return _outcome(self._graph.invoke(None, config))
            if waiting:
                # The graph never reached its pause: the process died after `request_approval`
                # saved the approval but before the checkpoint. The decision cannot be handed
                # over yet, and treating it as handed over would return the stale "waiting for
                # approval" reply and never apply it. Run the missing steps up to the pause
                # (`request_approval` reuses the saved approval), then look again.
                self._graph.invoke(None, config)
                continue
            # The graph already finished (the process died before the bookkeeping): do not apply
            # the decision a second time, but make sure the result is this approval's.
            _same_approval(snapshot.values.get("decided_approval_id"), approval_id)
            return _outcome(snapshot.values)
        raise RuntimeError("the conversation did not reach its approval pause")


def _same_approval(actual: object, expected: str) -> None:
    if actual != expected:
        raise RuntimeError("the conversation belongs to a different approval")


def _outcome(values: dict[str, Any]) -> ApprovalOutcome:
    return ApprovalOutcome(reply=str(values["reply"]), outcome=str(values["outcome"]))
