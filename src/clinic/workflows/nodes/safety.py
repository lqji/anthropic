from langchain_core.messages import AIMessage

from clinic.workflows.nodes._stub import NOT_IMPLEMENTED_REPLY, not_implemented
from clinic.workflows.state import ConversationState


def decline(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-09 declines medical advice and offers an appointment)."""
    return not_implemented()


def finalize(state: ConversationState) -> dict[str, object]:
    """Append this turn's reply to the history. CFD-09 adds the output guard here."""
    return {"messages": [AIMessage(content=state.get("reply") or NOT_IMPLEMENTED_REPLY)]}
