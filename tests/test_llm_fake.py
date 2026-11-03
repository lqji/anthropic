from collections.abc import Callable
from datetime import date

import pytest

from clinic.config import Settings
from clinic.errors import LLMUnavailable
from clinic.llm import (
    AnswerResult,
    ClassifyResult,
    FakeLLM,
    OpenAILLM,
    Passage,
    ScriptedFakeLLM,
    make_llm,
)

MONDAY = date(2026, 11, 2)


def test_fake_classify_is_deterministic(fake_llm: FakeLLM) -> None:
    text = "When is Dr. Salma free on Thursday?"
    results = [fake_llm.classify(text, MONDAY) for _ in range(3)]
    assert results[0].intent == "find_slots"
    assert results[0].doctor == "Dr. Salma Nasser"
    assert results[0].date == "2026-11-05"
    assert results[0] == results[1] == results[2]


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("I want to cancel my appointment", "cancel"),
        ("Please book me with Dr. Salma tomorrow", "book"),
        ("What are your opening hours?", "ask_clinic"),
        ("What is the cancellation policy?", "ask_clinic"),
        ("Any slots on Friday?", "find_slots"),
        ("hello", "other"),
        ("asdf qwer", "unclear"),
        ("", "unclear"),
    ],
)
def test_fake_classify_intents(fake_llm: FakeLLM, text: str, intent: str) -> None:
    assert fake_llm.classify(text, MONDAY).intent == intent


def test_fake_answer_names_the_best_passage(fake_llm: FakeLLM) -> None:
    passages = [
        Passage(source="insurance.md", text="We accept Omani insurance cards."),
        Passage(source="opening-hours.md", text="The clinic opens at 8 am on weekdays."),
    ]
    result = fake_llm.answer("What time does the clinic open?", passages)
    assert result == AnswerResult(
        found=True, text="The clinic opens at 8 am on weekdays.", source="opening-hours.md"
    )


def test_fake_answer_not_found_when_nothing_matches(fake_llm: FakeLLM) -> None:
    passages = [Passage(source="insurance.md", text="We accept Omani insurance cards.")]
    assert fake_llm.answer("Do you sell parking permits?", passages).found is False
    assert fake_llm.answer("anything", []).found is False


def test_scripted_fake_returns_in_order_and_records_calls() -> None:
    first = ClassifyResult(intent="book")
    llm = ScriptedFakeLLM(
        classify=[first, LLMUnavailable("down")], answer=[AnswerResult(found=False)]
    )
    assert llm.classify("one", MONDAY) == first
    with pytest.raises(LLMUnavailable):
        llm.classify("two", MONDAY)
    assert llm.answer("q", [Passage(source="s", text="t")]).found is False
    assert llm.classify_calls == [("one", MONDAY), ("two", MONDAY)]
    assert llm.answer_calls == [("q", (Passage(source="s", text="t"),))]


def test_scripted_fake_fails_loudly_when_script_runs_out() -> None:
    with pytest.raises(AssertionError, match="classify"):
        ScriptedFakeLLM().classify("x", MONDAY)


def test_make_llm_returns_fake_for_fake_provider(make_settings: Callable[..., Settings]) -> None:
    assert isinstance(make_llm(make_settings(llm_provider="fake")), FakeLLM)


def test_make_llm_returns_openai_adapter_for_openai_provider(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(llm_provider="openai", openai_api_key="sk-test-not-real")
    assert isinstance(make_llm(settings), OpenAILLM)
