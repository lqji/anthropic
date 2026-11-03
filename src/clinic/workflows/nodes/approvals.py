from clinic.workflows.approval import ApprovalNodes
from clinic.workflows.bookings import PostgresBookings
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.nodes._types import Node
from clinic.workflows.state import ConversationState


def cancel(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies (routing-only tests)."""
    return not_implemented()


def request_approval(state: ConversationState) -> dict[str, object]:
    """Stub (the real one stores a pending approval)."""
    return {}


def await_approval(state: ConversationState) -> dict[str, object]:
    """Stub (the real one pauses here with a real `interrupt()` until staff decide)."""
    return {}


def apply_decision(state: ConversationState) -> dict[str, object]:
    """Stub (the real one applies the staff decision for any approval kind)."""
    return {}


def make_approval_nodes(deps: WorkflowDeps) -> dict[str, Node]:
    """The real cancellation flow (CFD-14) over PostgreSQL bookings.

    `cancel` reads the day the patient named from `entities["date"]` (set by `classify`).
    """
    nodes = ApprovalNodes(
        deps.pool,
        deps.clock,
        PostgresBookings(deps.pool, deps.clock, deps.settings.clinic_timezone),
    )

    def real_cancel(state: ConversationState) -> dict[str, object]:
        asked: ConversationState = {**state, "target_date": state.get("entities", {}).get("date")}
        return nodes.cancel(asked)

    return {
        "cancel": real_cancel,
        "request_approval": nodes.request_approval,
        "await_approval": nodes.await_approval,
        "apply_decision": nodes.apply_decision,
    }
