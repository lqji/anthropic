from datetime import date
from typing import Final

from langchain_core.messages import HumanMessage

from clinic.domain.catalogue import match_name
from clinic.domain.dates import today_in
from clinic.llm import ClassifyContext, ClassifyResult, HistoryMessage
from clinic.repositories import appointments
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.history import history_window
from clinic.workflows.nodes._types import Node
from clinic.workflows.routing import latest_patient_text
from clinic.workflows.state import (
    UNCLEAR,
    UNKNOWN_DOCTOR,
    UNKNOWN_SPECIALTY,
    VALID_CHOICES,
    ConversationState,
)

_MAX_UNKNOWN_CHARS: Final = 80


def classify(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies (routing-only tests)."""
    return {"intent": UNCLEAR, "confidence": 0.0, "entities": {}}


def make_classify(deps: WorkflowDeps) -> Node:
    """Build the real `classify` node.

    It sends the model the latest message, at most `HISTORY_WINDOW` messages of history, today's
    date at the clinic and the catalogue. The model's doctor and specialty are checked against the
    catalogue in code, so the nodes after it only ever see exact catalogue names.
    """
    timezone = deps.settings.clinic_timezone

    def classify(state: ConversationState) -> dict[str, object]:
        with deps.pool.connection() as conn:
            doctors = tuple(doctor.name for doctor in appointments.list_doctors(conn))
            specialties = tuple(sp.name for sp in appointments.list_specialties(conn))
        window = history_window(state.get("messages", []), deps.settings.history_window)
        earlier = window[:-1] if window and isinstance(window[-1], HumanMessage) else window
        context = ClassifyContext(
            today=today_in(deps.clock, timezone),
            history=tuple(
                HistoryMessage(
                    role="patient" if isinstance(message, HumanMessage) else "assistant",
                    text=message.text,
                )
                for message in earlier
            ),
            doctors=doctors,
            specialties=specialties,
        )
        result = deps.llm.classify(latest_patient_text(state), context)
        return {
            "intent": result.intent,
            "confidence": result.confidence,
            "entities": _entities(result, doctors, specialties),
        }

    return classify


def _entities(
    result: ClassifyResult, doctors: tuple[str, ...], specialties: tuple[str, ...]
) -> dict[str, str]:
    entities: dict[str, str] = {}
    unknown = False
    for value, names, key, unknown_key in (
        (result.doctor, doctors, "doctor", UNKNOWN_DOCTOR),
        (result.specialty, specialties, "specialty", UNKNOWN_SPECIALTY),
    ):
        if not value:
            continue
        name = match_name(value, names)
        if name is not None:
            entities[key] = name
        else:
            entities[unknown_key] = value.strip()[:_MAX_UNKNOWN_CHARS]
            unknown = True
    if unknown and (doctors or specialties):
        entities[VALID_CHOICES] = (
            f"doctors: {', '.join(doctors)}; specialties: {', '.join(specialties)}"
        )
    if result.date and _is_iso_date(result.date):
        entities["date"] = result.date
    return entities


def _is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
