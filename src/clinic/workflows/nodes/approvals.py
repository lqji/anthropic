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
    """Stub used when the graph is built without dependencies."""
    return {}


def await_approval(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies."""
    return {}


def apply_decision(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies."""
    return {}


def make_approval_nodes(deps: WorkflowDeps) -> dict[str, Node]:
    """The real approval nodes (CFD-14), cancelling bookings in PostgreSQL."""
    bookings = PostgresBookings(deps.pool, deps.clock, deps.settings.clinic_timezone)
    nodes = ApprovalNodes(deps.pool, deps.clock, bookings)

    def cancel(state: ConversationState) -> dict[str, object]:
        """The date comes from the intent step's entities."""
        with_date: ConversationState = {
            **state,
            "target_date": state.get("entities", {}).get("date"),
        }
        return nodes.cancel(with_date)

    return {
        "cancel": cancel,
        "request_approval": nodes.request_approval,
        "await_approval": nodes.await_approval,
        "apply_decision": nodes.apply_decision,
    }
