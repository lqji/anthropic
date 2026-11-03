import logging
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion

from clinic.app import create_app
from clinic.config import Settings
from clinic.errors import LLMUnavailable
from clinic.llm import (
    AnswerResult,
    ClassifyContext,
    ClassifyResult,
    HistoryMessage,
    OpenAILLM,
    Passage,
)
from clinic.llm import openai_adapter as adapter

MONDAY = date(2026, 11, 2)
CONTEXT = ClassifyContext(
    today=MONDAY,
    history=(
        HistoryMessage(role="patient", text="Is Dr. Salma in?"),
        HistoryMessage(role="assistant", text="Which day?"),
    ),
    doctors=("Dr. Salma Nasser", "Dr. Khalid Rahman"),
    specialties=("Dermatology", "General Practice"),
)
PASSAGES = [Passage(source="opening-hours.md", text="The clinic opens at 8 am.")]
_REQUEST = httpx2.Request("POST", "https://api.openai.test/v1/chat/completions")


def _response(status: int) -> httpx2.Response:
    return httpx2.Response(status, request=_REQUEST)


def _completion() -> ChatCompletion:
    return ChatCompletion(id="c", choices=[], created=0, model="m", object="chat.completion")


PROVIDER_FAILURES = [
    openai.APITimeoutError(request=_REQUEST),
    openai.APIConnectionError(request=_REQUEST),
    openai.RateLimitError("slow down", response=_response(429), body=None),
    openai.InternalServerError("boom", response=_response(500), body=None),
    openai.LengthFinishReasonError(completion=_completion()),
]


class StubClient:
    """Stands in for `OpenAI`: returns one parsed object or raises one error."""

    def __init__(self, parsed: object = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._parsed = parsed
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        message = SimpleNamespace(parsed=self._parsed)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def openai_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(llm_provider="openai", openai_api_key="sk-test-not-real")


def _llm(settings: Settings, client: StubClient) -> OpenAILLM:
    return OpenAILLM(settings, client=client)  # type: ignore[arg-type]  # duck-typed stub


@pytest.mark.parametrize("error", PROVIDER_FAILURES, ids=lambda e: type(e).__name__)
def test_provider_failures_become_llm_unavailable_and_are_logged(
    openai_settings: Settings, error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    llm = _llm(openai_settings, StubClient(error=error))
    with caplog.at_level(logging.INFO, logger="clinic.llm"), pytest.raises(LLMUnavailable):
        llm.classify("hello", CONTEXT)
    records = [r for r in caplog.records if r.name == "clinic.llm"]
    assert len(records) == 1
    assert records[0].getMessage() == "llm.call"
    assert records[0].fields["outcome"] == "error"  # type: ignore[attr-defined]  # via `extra`
    assert records[0].fields["error"] == type(error).__name__  # type: ignore[attr-defined]  # via `extra`


def test_answer_provider_failure_also_raises(openai_settings: Settings) -> None:
    llm = _llm(openai_settings, StubClient(error=openai.APITimeoutError(request=_REQUEST)))
    with pytest.raises(LLMUnavailable):
        llm.answer("When do you open?", PASSAGES)


def test_content_filter_means_unclear_and_not_found(openai_settings: Settings) -> None:
    error = openai.ContentFilterFinishReasonError()
    llm = _llm(openai_settings, StubClient(error=error))
    assert llm.classify("hello", CONTEXT) == ClassifyResult(intent="unclear", confidence=0.0)
    assert llm.answer("When do you open?", PASSAGES) == AnswerResult(found=False)


def test_llm_unavailable_maps_to_503_json(openai_settings: Settings) -> None:
    app = create_app(openai_settings)
    llm = _llm(openai_settings, StubClient(error=openai.APITimeoutError(request=_REQUEST)))

    @app.get("/boom")
    def boom() -> None:
        llm.classify("hello", CONTEXT)

    with TestClient(app) as client:
        response = client.get("/boom")
    assert response.status_code == 503
    assert response.json()["code"] == "llm_unavailable"


def test_classify_maps_the_parsed_output(openai_settings: Settings) -> None:
    parsed = adapter._ClassifyOut(
        intent="find_slots",
        doctor="Dr. Salma",
        specialty=None,
        date="2026-11-05",
        confidence=0.9,
    )
    client = StubClient(parsed=parsed)
    result = _llm(openai_settings, client).classify("Salma Thursday?", CONTEXT)
    assert result == ClassifyResult(
        intent="find_slots", doctor="Dr. Salma", date="2026-11-05", confidence=0.9
    )
    assert client.calls[0]["model"] == openai_settings.openai_model
    assert client.calls[0]["messages"][-1] == {"role": "user", "content": "Salma Thursday?"}
    assert result.specialty is None


def test_classify_prompt_carries_today_the_catalogue_and_the_history(
    openai_settings: Settings,
) -> None:
    parsed = adapter._ClassifyOut(
        intent="other", doctor=None, specialty=None, date=None, confidence=1.0
    )
    client = StubClient(parsed=parsed)
    _llm(openai_settings, client).classify("hello", CONTEXT)
    messages = client.calls[0]["messages"]
    system = messages[0]["content"]
    assert messages[0]["role"] == "system"
    assert "2026-11-02 (Monday)" in system
    assert "Dr. Salma Nasser, Dr. Khalid Rahman" in system
    assert "Dermatology, General Practice" in system
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "Is Dr. Salma in?"),
        ("assistant", "Which day?"),
        ("user", "hello"),
    ]


def test_classify_drops_an_invalid_date_and_clamps_confidence(openai_settings: Settings) -> None:
    parsed = adapter._ClassifyOut(
        intent="book", doctor="", specialty="", date="next week", confidence=7
    )
    result = _llm(openai_settings, StubClient(parsed=parsed)).classify("book", CONTEXT)
    assert result.date is None
    assert result.doctor is None
    assert result.confidence == 1.0


def test_classify_without_parsed_output_is_unclear(openai_settings: Settings) -> None:
    result = _llm(openai_settings, StubClient(parsed=None)).classify("hello", CONTEXT)
    assert result.intent == "unclear"


def test_answer_accepts_a_grounded_answer(openai_settings: Settings) -> None:
    parsed = adapter._AnswerOut(found=True, text="We open at 8 am.", source="opening-hours.md")
    result = _llm(openai_settings, StubClient(parsed=parsed)).answer("When open?", PASSAGES)
    assert result == AnswerResult(found=True, text="We open at 8 am.", source="opening-hours.md")


@pytest.mark.parametrize(
    "parsed",
    [
        None,
        adapter._AnswerOut(found=False, text="", source=None),
        adapter._AnswerOut(found=True, text="  ", source="opening-hours.md"),
        adapter._AnswerOut(found=True, text="We open at 8 am.", source="invented.md"),
        adapter._AnswerOut(found=True, text="We open at 8 am.", source=None),
    ],
)
def test_answer_guard_rejects_ungrounded_output(
    openai_settings: Settings, parsed: adapter._AnswerOut | None
) -> None:
    result = _llm(openai_settings, StubClient(parsed=parsed)).answer("When open?", PASSAGES)
    assert result == AnswerResult(found=False)


def test_client_is_built_with_timeout_and_retries(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, Any]] = []

    def fake_openai(**kwargs: Any) -> object:
        built.append(kwargs)
        return object()

    monkeypatch.setattr(adapter, "OpenAI", fake_openai)
    OpenAILLM(make_settings(llm_provider="openai", openai_api_key="sk-test-not-real"))
    assert built == [{"api_key": "sk-test-not-real", "timeout": 20.0, "max_retries": 1}]

    built.clear()
    OpenAILLM(
        make_settings(
            llm_provider="openai",
            openai_api_key="sk-test-not-real",
            llm_timeout_s=7,
            llm_max_retries=0,
        )
    )
    assert built[0]["timeout"] == 7
    assert built[0]["max_retries"] == 0


def test_openai_adapter_cannot_be_built_with_the_fake_provider(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(RuntimeError, match="LLM_PROVIDER=openai"):
        OpenAILLM(make_settings(llm_provider="fake"))


def test_every_system_prompt_carries_the_no_diagnosis_rule(openai_settings: Settings) -> None:
    classified = adapter._ClassifyOut(
        intent="other", doctor=None, specialty=None, date=None, confidence=1.0
    )
    answered = adapter._AnswerOut(found=True, text="We open at 8 am.", source="opening-hours.md")
    classify_client = StubClient(parsed=classified)
    answer_client = StubClient(parsed=answered)

    _llm(openai_settings, classify_client).classify("hello", CONTEXT)
    _llm(openai_settings, answer_client).answer("When open?", PASSAGES)

    for client in (classify_client, answer_client):
        system = client.calls[0]["messages"][0]
        assert system["role"] == "system"
        assert adapter.NO_DIAGNOSIS_RULE in system["content"]
