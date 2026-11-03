from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.state import ConversationState


def cancel(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-14 finds the booking to cancel)."""
    return not_implemented()


def request_approval(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-14 stores a pending approval)."""
    return {}


def await_approval(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-14 pauses here with a real `interrupt()` until staff decide)."""
    return {}


def apply_decision(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-14 applies the staff decision for any approval kind)."""
    return {}
