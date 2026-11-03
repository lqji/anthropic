import datetime as dt
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Emergencies are not an intent: `is_emergency()` is deterministic code and runs before the model.
Intent = Literal["find_slots", "book", "cancel", "clinic_question", "other", "unclear"]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["patient", "assistant"]
    text: str


class ClassifyContext(BaseModel):
    """What the classifier may use besides the message: earlier turns, today, the catalogue."""

    model_config = ConfigDict(frozen=True)

    today: dt.date  # the date at the clinic
    history: tuple[HistoryMessage, ...] = ()  # earlier messages, oldest first, without this one
    doctors: tuple[str, ...] = ()
    specialties: tuple[str, ...] = ()


class ClassifyResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: Intent
    doctor: str | None = None
    specialty: str | None = None
    date: str | None = None  # ISO date, YYYY-MM-DD
    confidence: float = Field(default=1.0, ge=0, le=1)


class Passage(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    text: str


class AnswerResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    found: bool
    text: str = ""
    source: str | None = None


class LLMPort(Protocol):
    """The only way workflows talk to a language model."""

    def classify(self, text: str, context: ClassifyContext) -> ClassifyResult:
        """Intent and entities of a patient message, given the earlier turns and the catalogue."""
        ...

    def answer(self, question: str, passages: Sequence[Passage]) -> AnswerResult:
        """Answer from the given clinic passages only; found=False when they do not cover it."""
        ...
