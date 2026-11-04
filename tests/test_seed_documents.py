"""The seed clinic documents (seed/clinic_docs) on a real PostgreSQL.

Every document loads into chunks, each document added in CFD-34 answers a question and is named as
the source, and the questions the documents do not cover still get the "not covered" reply.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest

from clinic.app import prepare_database
from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.llm import FakeLLM
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._types import Node
from clinic.workflows.nodes.knowledge import NOT_IN_DOCUMENTS_REPLY, make_knowledge
from clinic.workflows.state import new_turn
from tests.conftest import temporary_database

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "clinic_docs"
SEED_FILES = sorted(path.name for path in SEED_DIR.glob("*.md"))
# document added in CFD-34 -> (a question it answers, the section that answers it)
NEW_DOCUMENTS = {
    "new-patients.md": ("How do I register as a new patient?", "Registering with the clinic"),
    "payment-and-fees.md": (
        "Can I get an invoice or a receipt for my payment?",
        "Receipts and invoices",
    ),
}
# Not covered by any document; also used by tests/test_knowledge.py and scripts/smoke_test.sh.
NOT_COVERED = [
    "Do you offer laser eye surgery?",
    "Can you test my eyes?",
    "Where can I park my car?",
]


@pytest.fixture
def settings(make_settings: Callable[..., Settings], database_url: str) -> Iterator[Settings]:
    with temporary_database(database_url) as url:
        settings = make_settings(database_url=url)
        assert prepare_database(settings, FixedClock(NOW))  # tables, seed, clinic documents
        yield settings


@pytest.fixture
def knowledge(settings: Settings) -> Iterator[Node]:
    """The real `knowledge` node with FakeLLM, as the service builds it."""
    pool = make_pool(settings)
    pool.open(wait=True, timeout=10)
    try:
        yield make_knowledge(
            WorkflowDeps(settings=settings, pool=pool, clock=FixedClock(NOW), llm=FakeLLM())
        )
    finally:
        pool.close(timeout=5)


def test_there_are_six_seed_documents_including_the_new_ones() -> None:
    assert len(SEED_FILES) == 6
    assert set(NEW_DOCUMENTS) <= set(SEED_FILES)


def test_every_seed_document_loads_into_chunks(settings: Settings) -> None:
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        rows = conn.execute(
            """
            SELECT d.name, count(c.id)
            FROM documents d LEFT JOIN doc_chunks c ON c.document_id = d.id
            GROUP BY d.name ORDER BY d.name
            """
        ).fetchall()

    assert [name for name, _ in rows] == SEED_FILES
    assert all(chunks > 0 for _, chunks in rows), rows


@pytest.mark.parametrize("document", sorted(NEW_DOCUMENTS))
def test_each_new_document_answers_a_question_and_is_named_as_the_source(
    knowledge: Node, document: str
) -> None:
    question, heading = NEW_DOCUMENTS[document]

    update = knowledge(new_turn(question, "t1"))

    assert update["outcome"] == "answered"
    assert str(update["reply"]).endswith(f"\n\nSource: {document}")
    sources = update["sources"]
    assert isinstance(sources, list)
    assert {"document": document, "heading": heading} in sources


@pytest.mark.parametrize("question", NOT_COVERED)
def test_questions_the_documents_do_not_cover_still_get_the_not_covered_reply(
    knowledge: Node, question: str
) -> None:
    update = knowledge(new_turn(question, "t1"))

    assert update["outcome"] == "not_in_documents"
    assert update["reply"] == NOT_IN_DOCUMENTS_REPLY
    assert update["sources"] == []
