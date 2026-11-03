from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.state import ConversationState


def clarify(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-07 asks one clarifying question)."""
    return not_implemented()


def other(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-07 replies with the templated capabilities message)."""
    return not_implemented()
