import datetime as dt
import re
from collections import deque
from collections.abc import Sequence

from clinic.domain.catalogue import name_tokens
from clinic.domain.dates import resolve_date
from clinic.llm.port import AnswerResult, ClassifyContext, ClassifyResult, Intent, Passage

_WORD = re.compile(r"[a-z']+")
_STOPWORDS = frozenset(
    "a an and are at be can do does for how i in is it me my of on or the to what when where "
    "which who you your".split()
)
_CANCEL = frozenset({"cancel", "cancelling", "canceling"})
_BOOK = frozenset({"book", "reserve", "schedule"})
_ASK_CLINIC = frozenset(
    {
        "open",
        "hours",
        "price",
        "cost",
        "insurance",
        "policy",
        "where",
        "location",
        "parking",
        "prepare",
        "preparation",
    }
)
_FIND_SLOTS = frozenset({"free", "available", "availability", "slot", "slots", "appointment"})
_GREETING = frozenset({"hello", "hi", "hey", "thanks", "thank"})


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


_DOCTOR_TITLE = re.compile(r"\bdr\.?\s+([a-z]+)")


def _find_doctor(text: str, words: set[str], doctors: Sequence[str]) -> str | None:
    """A catalogue doctor whose name the text mentions, else "Dr. X" for an unlisted "Dr X"."""
    listed = [name for name in doctors if name_tokens(name) & words]
    if len(listed) == 1:
        return listed[0]
    unlisted = _DOCTOR_TITLE.search(text.lower())
    return f"Dr. {unlisted[1].capitalize()}" if unlisted and not listed else None


def _find_specialty(text: str, specialties: Sequence[str]) -> str | None:
    lowered = text.lower()
    return next((name for name in specialties if name.lower() in lowered), None)


def _intent(words: set[str], doctor: str | None, resolved: dt.date | None) -> Intent:
    if not words:
        return "unclear"
    if words & _CANCEL:
        return "cancel"
    if words & _BOOK:
        return "book"
    if words & _ASK_CLINIC:
        return "clinic_question"
    if words & _FIND_SLOTS or (doctor and resolved):
        return "find_slots"
    if words & _GREETING:
        return "other"
    return "unclear"


class FakeLLM:
    """Deterministic stand-in for the model: keyword rules, same answer on every run."""

    def classify(self, text: str, context: ClassifyContext) -> ClassifyResult:
        words = _words(text)
        doctor = _find_doctor(text, words, context.doctors)
        specialty = _find_specialty(text, context.specialties)
        resolved = resolve_date(text, context.today)
        intent = _intent(words, doctor, resolved)
        return ClassifyResult(
            intent=intent,
            doctor=doctor,
            specialty=specialty,
            date=resolved.isoformat() if resolved else None,
            confidence=0.0 if intent == "unclear" else 1.0,
        )

    def answer(self, question: str, passages: Sequence[Passage]) -> AnswerResult:
        wanted = _words(question) - _STOPWORDS
        best: Passage | None = None
        best_score = 0
        for passage in passages:
            score = len(wanted & (_words(passage.text) - _STOPWORDS))
            if score > best_score:
                best, best_score = passage, score
        if best is None:
            return AnswerResult(found=False)
        return AnswerResult(found=True, text=best.text, source=best.source)


def _next[T](queue: deque[T | Exception], operation: str) -> T:
    if not queue:
        raise AssertionError(f"ScriptedFakeLLM has no {operation} result left")
    item = queue.popleft()
    if isinstance(item, Exception):
        raise item
    return item


class ScriptedFakeLLM:
    """Returns queued results in order (or raises a queued exception) and records every call."""

    def __init__(
        self,
        classify: Sequence[ClassifyResult | Exception] = (),
        answer: Sequence[AnswerResult | Exception] = (),
    ) -> None:
        self._classify: deque[ClassifyResult | Exception] = deque(classify)
        self._answer: deque[AnswerResult | Exception] = deque(answer)
        self.classify_calls: list[tuple[str, ClassifyContext]] = []
        self.answer_calls: list[tuple[str, tuple[Passage, ...]]] = []

    def classify(self, text: str, context: ClassifyContext) -> ClassifyResult:
        self.classify_calls.append((text, context))
        return _next(self._classify, "classify")

    def answer(self, question: str, passages: Sequence[Passage]) -> AnswerResult:
        self.answer_calls.append((question, tuple(passages)))
        return _next(self._answer, "answer")
