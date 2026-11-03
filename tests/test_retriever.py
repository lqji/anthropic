"""`rag.retriever.search` on a real PostgreSQL loaded with the seed clinic documents."""

from collections.abc import Iterator
from datetime import UTC, datetime

import psycopg
import pytest

from clinic.app import prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.rag.retriever import MIN_MATCHED_TERMS, Chunk, search
from tests.conftest import temporary_database

MIN_SCORE = 0.30
TOP_K = 3


@pytest.fixture(scope="module")
def conn(database_url: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with temporary_database(database_url) as url:
        settings = Settings(llm_provider="fake", database_url=url)
        assert prepare_database(settings, FixedClock(datetime(2026, 11, 2, 8, tzinfo=UTC)))
        with psycopg.connect(url) as connection:
            yield connection


def _search(
    conn: psycopg.Connection[tuple[object, ...]],
    question: str,
    min_score: float = MIN_SCORE,
    top_k: int = TOP_K,
) -> list[Chunk]:
    return search(conn, question, min_score=min_score, top_k=top_k)


def _assert_qualifies(chunks: list[Chunk], min_score: float = MIN_SCORE) -> None:
    scores = [chunk.score for chunk in chunks]
    assert scores == sorted(scores, reverse=True)
    assert all(chunk.score >= min_score for chunk in chunks)
    assert all(chunk.matched_terms >= MIN_MATCHED_TERMS for chunk in chunks)


def test_blood_test_question_finds_the_blood_tests_section_first(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    chunks = _search(conn, "Do I need to fast before a blood test?")

    assert (chunks[0].document, chunks[0].heading) == ("visit-preparation.md", "Blood tests")
    assert "fasting" in chunks[0].content
    _assert_qualifies(chunks)


@pytest.mark.parametrize("question", ["Do you offer laser eye surgery?", "Can you test my eyes?"])
def test_questions_the_documents_do_not_cover_find_nothing(
    conn: psycopg.Connection[tuple[object, ...]], question: str
) -> None:
    assert _search(conn, question) == []


def test_one_shared_word_is_not_enough_even_with_a_high_score(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    """The word "test" alone ranks the blood-test section above the threshold: still no answer."""
    assert _search(conn, "Can you test my eyes?", min_score=0.0) == []
    assert _search(conn, "test", min_score=0.0) == []


@pytest.mark.parametrize(
    "question", ["What is it?", "the and of", "???", "   ", "it's & | ! ( ) :* '"]
)
def test_stop_words_or_punctuation_only_find_nothing_without_error(
    conn: psycopg.Connection[tuple[object, ...]], question: str
) -> None:
    assert _search(conn, question) == []


def test_at_most_top_k_chunks_ordered_by_score(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    question = "Do you accept my insurance?"
    every = _search(conn, question, top_k=50)
    two = _search(conn, question, top_k=2)

    assert len(every) > 2
    assert two == every[:2]
    assert {chunk.document for chunk in every} == {"insurance.md"}
    _assert_qualifies(every)


def test_chunks_below_the_minimum_score_are_left_out(
    conn: psycopg.Connection[tuple[object, ...]],
) -> None:
    question = "Do I need to fast before a blood test?"
    best = _search(conn, question)[0].score

    assert _search(conn, question, min_score=best + 0.01) == []
    assert _search(conn, question, min_score=best)[0].score == best


@pytest.mark.parametrize(
    ("question", "document"),
    [
        ("What is your cancellation policy?", "cancellation-policy.md"),
        ("How should I prepare for my visit?", "visit-preparation.md"),
        ("What time do you open on Sunday?", "opening-hours.md"),
    ],
)
def test_the_document_name_counts_as_searchable_words(
    conn: psycopg.Connection[tuple[object, ...]], question: str, document: str
) -> None:
    chunks = _search(conn, question)

    assert chunks
    assert chunks[0].document == document
    _assert_qualifies(chunks)
