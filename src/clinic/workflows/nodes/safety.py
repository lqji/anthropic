from langchain_core.messages import AIMessage

from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.state import ConversationState


class MissingReplyError(RuntimeError):
    """A turn reached `finalize` without a reply: a bug in the node that handled it."""


def decline(state: ConversationState) -> dict[str, object]:
    """Stub (CFD-09 declines medical advice and offers an appointment)."""
    return not_implemented()


def finalize(state: ConversationState) -> dict[str, object]:
    """Append this turn's reply to the history. CFD-09 adds the output guard here.

    Every path must set `reply`. A missing one fails the turn loudly (500, logged) instead of
    being papered over with a generic message. The reply's id comes from the turn id, so a
    replayed turn replaces its earlier reply.
    """
    reply = state.get("reply", "")
    if not reply.strip():
        raise MissingReplyError(f"turn ended without a reply (outcome={state.get('outcome')!r})")
    return {"messages": [AIMessage(content=reply, id=f"{state['turn_id']}:reply")]}
