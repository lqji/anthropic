"""The R3 experiment (experiments/rag) still runs and still shows what the research note says."""

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from clinic.workflows.nodes.knowledge import NOT_IN_DOCUMENTS_REPLY
from tests.conftest import temporary_database

DEMO = Path(__file__).resolve().parent.parent / "experiments" / "rag" / "rag_demo.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rag_demo", DEMO)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered by name, as a normal import would be: its dataclasses look their module up there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def url(database_url: str) -> Iterator[str]:
    with temporary_database(database_url) as url:
        yield url


def _line(seen: list[str], start: str) -> str:
    [line] = [line for line in seen if line.startswith(start)]
    return line


def test_postgres_search_finds_each_document_and_keyword_overlap_does_not(url: str) -> None:
    seen = _load().run(url)
    text = "\n".join(seen)

    assert "0. 15 chunks: cancellation-policy.md=4, insurance.md=4" in text
    # Option B (the service's retriever) is right on six of the seven questions.
    assert "right: A keyword 3/7, B postgres 6/7" in text
    for expected in ("visit-preparation", "opening-hours", "insurance", "cancellation-policy"):
        assert f"B postgres: right | {expected}.md / " in text
    # Option A answers both uncovered questions, from documents that do not cover them.
    assert "A keyword:  WRONG: would answer from cancellation-policy.md" in text
    assert "A keyword:  WRONG: would answer from visit-preparation.md" in text


def test_uncovered_questions_find_no_chunk_and_get_the_not_covered_reply(url: str) -> None:
    seen = _load().run(url)

    assert seen.count("     B postgres: right (nothing found) | -") == 2
    for number in (5, 6):
        assert _line(seen, f"   1.{number}: ") == f"   1.{number}: 0 / 0 / 0 / 0 / 0"
        assert _line(seen, f"6.{number} ") == f"6.{number} not_in_documents: " + repr(
            NOT_IN_DOCUMENTS_REPLY
        )
    # Covered questions are answered with the right source.
    assert _line(seen, "6.1 ").endswith("last line 'Source: visit-preparation.md'")


def test_the_one_word_question_is_stopped_by_the_two_word_rule(url: str) -> None:
    seen = _load().run(url)
    text = "\n".join(seen)

    # Without the rule, "Can you test my eyes?" passes the 0.30 threshold on one shared word.
    assert "3. rule off, 1.6: 3 / 1 / 1 / 0 / 0" in text
    assert "at 0.30: WRONG: would answer from visit-preparation.md" in text
    assert "visit-preparation.md / Blood tests 0.69 (1w)" in text


def test_the_two_word_rule_also_misses_a_covered_one_word_question(url: str) -> None:
    seen = _load().run(url)
    text = "\n".join(seen)

    # "What should I bring?" has one searchable word: the rule drops it although it is covered,
    # and without the rule it scores lower (0.58) than the wrong one-word match above (0.69).
    assert "     B postgres: MISSED | -" in text
    assert "3. rule off, 1.7: 3 / 1 / 1 / 0 / 0" in text
    assert "at 0.30: right | visit-preparation.md / What to bring 0.58 (1w)" in text
    assert _line(seen, "6.7 ").startswith("6.7 not_in_documents: ")


def test_the_experiment_can_run_again_on_the_same_database(url: str) -> None:
    demo = _load()

    assert demo.run(url) == demo.run(url)
