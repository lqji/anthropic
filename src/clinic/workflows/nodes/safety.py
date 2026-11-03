import logging
from typing import Final

from langchain_core.messages import AIMessage

from clinic.domain.guard import guard_reply
from clinic.workflows.state import ConversationState

logger = logging.getLogger("clinic.safety")

DECLINED_MEDICAL_ADVICE: Final = "declined_medical_advice"
DECLINE_REPLY: Final = (
    "I'm sorry, I can't give medical advice or suggest treatments. Please speak to a doctor. "
    "I can help you find an appointment: tell me the doctor or specialty and the day you prefer."
)


class MissingReplyError(RuntimeError):
    """A turn reached `finalize` without a reply: a bug in the node that handled it."""


def decline(state: ConversationState) -> dict[str, object]:
    """Fixed reply to a request for medical advice. No LLM, no clinic documents, no database."""
    return {"reply": DECLINE_REPLY, "outcome": DECLINED_MEDICAL_ADVICE}


def finalize(state: ConversationState) -> dict[str, object]:
    """Output guard, then append this turn's reply to the history.

    A reply that diagnoses, recommends treatment or gives a dosage is replaced by the decline
    reply. The log names the rule only, never the reply or the patient's words.

    Every path must set `reply`. A missing one fails the turn loudly (500, logged) instead of
    being papered over with a generic message. The reply's id comes from the turn id, so a
    replayed turn replaces its earlier reply.
    """
    reply = state.get("reply", "")
    if not reply.strip():
        raise MissingReplyError(f"turn ended without a reply (outcome={state.get('outcome')!r})")
    reply, rule = guard_reply(reply, DECLINE_REPLY)
    update: dict[str, object] = {}
    if rule is not None:
        logger.warning("safety.guard_triggered", extra={"fields": {"rule": rule}})
        update.update(outcome=DECLINED_MEDICAL_ADVICE, sources=[])
    update["messages"] = [AIMessage(content=reply, id=f"{state['turn_id']}:reply")]
    return update
