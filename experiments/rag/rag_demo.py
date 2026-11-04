"""R3 experiment: which retrieval finds the right clinic document, and which one stays quiet when
the documents do not cover the question?

Two options run on the same chunks of the four clinic documents (seed/clinic_docs):

    A. keyword overlap in Python: count the question's words that appear in a chunk.
    B. the service's own PostgreSQL full-text search (`clinic.rag.retriever.search`).

Then it changes the thresholds (`RAG_MIN_SCORE`, the 2-matched-words rule, `RAG_TOP_K`), compares
heading sections with whole documents, and runs the real `knowledge` node with the project's
FakeLLM (no API key) to show the replies. Embeddings are compared on paper only (see
docs/research/R3-rag.md): they need an external API and a vector store, which the project forbids.

Run it (needs a PostgreSQL, for example the dev database from compose.dev.yaml):

    DATABASE_URL=postgresql://clinic:change-me-local-only@localhost:5432/clinic \\
        uv run python experiments/rag/rag_demo.py
"""

import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import psycopg
from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.clock import FixedClock
from clinic.config import Settings
from clinic.db import make_pool
from clinic.llm import FakeLLM
from clinic.rag import retriever
from clinic.rag.loader import KnowledgeLoader
from clinic.schema import apply_schema
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes.knowledge import make_knowledge
from clinic.workflows.state import new_turn

DOCS_DIR = Path(__file__).resolve().parents[2] / "seed" / "clinic_docs"
MIN_SCORE = 0.30  # the service's defaults (RAG_MIN_SCORE, RAG_TOP_K in .env.example)
TOP_K = 3
SCORES = (0.1, 0.3, 0.5, 0.7, 0.9)  # min_score values tried in steps 2 and 3

# (question, the document that should answer it; None = the documents do not cover it)
QUESTIONS: list[tuple[str, str | None]] = [
    ("Do I need to fast before a blood test?", "visit-preparation.md"),
    ("What are your opening hours?", "opening-hours.md"),
    ("Which insurers do you accept?", "insurance.md"),
    ("What is your cancellation policy?", "cancellation-policy.md"),
    ("Do you offer laser eye surgery?", None),  # no document mentions it
    ("Can you test my eyes?", None),  # shares one word ("test") with the documents
    ("What should I bring?", "visit-preparation.md"),  # covered, but only one searchable word
]

# Option A only. Small on purpose: the words a simple search would ignore.
STOP_WORDS = frozenset(
    "a an and are at be before can do does for how i in is it me my need of on or the to what "
    "when where which who you your".split()
)
_WORD = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Section:
    document: str
    heading: str
    content: str


@dataclass(frozen=True)
class Hit:
    document: str
    heading: str
    score: float
    matched: int  # how many of the question's words the chunk contains


def words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - STOP_WORDS


def n_words(text: str) -> int:
    return len(text.split())


def load_sections(conn: Connection[TupleRow]) -> list[Section]:
    """The chunks the loader stored, in document order: both options search these."""
    rows = conn.execute(
        """
        SELECT d.name, c.heading, c.content
        FROM doc_chunks c JOIN documents d ON d.id = c.document_id
        ORDER BY d.name, c.ordinal
        """
    ).fetchall()
    return [Section(*row) for row in rows]


def keyword_search(sections: list[Section], question: str, min_matched: int = 1) -> list[Hit]:
    """Option A: a chunk scores the share of the question's words found in its heading or text.

    Exact words only: no stemming, so "insurers" does not find "insurer" or "insurance".
    """
    wanted = words(question)
    hits = []
    for section in sections:
        matched = len(wanted & words(f"{section.heading} {section.content}"))
        if matched >= min_matched:
            hits.append(Hit(section.document, section.heading, matched / len(wanted), matched))
    hits.sort(key=lambda hit: -hit.score)  # stable sort: a tie keeps the document order
    return hits[:TOP_K]


def postgres_search(
    conn: Connection[TupleRow], question: str, *, min_score: float = MIN_SCORE, top_k: int = TOP_K
) -> list[Hit]:
    """Option B: the service's retriever, unchanged."""
    return [
        Hit(chunk.document, chunk.heading, chunk.score, chunk.matched_terms)
        for chunk in retriever.search(conn, question, min_score=min_score, top_k=top_k)
    ]


def verdict(hits: list[Hit], expected: str | None) -> str:
    """Judged on the best hit, the one an answer would most likely come from."""
    if not hits:
        return "right (nothing found)" if expected is None else "MISSED"
    if expected is None:
        return f"WRONG: would answer from {hits[0].document}"
    return "right" if hits[0].document == expected else f"WRONG: {hits[0].document}"


def show(hits: list[Hit]) -> str:
    if not hits:
        return "-"
    return "; ".join(f"{h.document} / {h.heading} {h.score:.2f} ({h.matched}w)" for h in hits)


def sweep(conn: Connection[TupleRow], question: str) -> str:
    """Chunks returned (top_k 3) at each min_score in SCORES."""
    return " / ".join(str(len(postgres_search(conn, question, min_score=m))) for m in SCORES)


def run(database_url: str) -> list[str]:
    """Run the whole experiment and return what it observed, one line per fact."""
    seen: list[str] = []
    with psycopg.connect(database_url, connect_timeout=10) as conn:  # every external call: timeout
        apply_schema(conn)  # the app's own tables (src/clinic/sql), as at startup
        KnowledgeLoader(DOCS_DIR).load(conn)  # the real loader: heading sections, max 200 words
        conn.commit()
        sections = load_sections(conn)

        # 0. What the loader stored.
        per_doc = {s.document: 0 for s in sections}
        for section in sections:
            per_doc[section.document] += 1
        loaded = ", ".join(f"{name}={count}" for name, count in per_doc.items())
        seen.append(f"0. {len(sections)} chunks: {loaded}")

        # 1. Option A against option B, with the service's thresholds.
        right = {"A": 0, "B": 0, "A2": 0}
        for number, (question, expected) in enumerate(QUESTIONS, start=1):
            keyword = keyword_search(sections, question)
            postgres = postgres_search(conn, question)
            seen.append(f"1.{number} {question!r} expected: {expected or 'not covered'}")
            seen.append(f"     A keyword:  {verdict(keyword, expected)} | {show(keyword)}")
            seen.append(f"     B postgres: {verdict(postgres, expected)} | {show(postgres)}")
            right["A"] += verdict(keyword, expected).startswith("right")
            right["B"] += verdict(postgres, expected).startswith("right")
            two_words = keyword_search(sections, question, min_matched=2)
            right["A2"] += verdict(two_words, expected).startswith("right")
        total = len(QUESTIONS)
        seen.append(f"   right: A keyword {right['A']}/{total}, B postgres {right['B']}/{total}")
        seen.append(f"   A keyword with the 2-word rule: {right['A2']}/{total}")

        # 2. RAG_MIN_SCORE, with the 2-word rule on (as in the service).
        seen.append("2. min_score 0.1 / 0.3 / 0.5 / 0.7 / 0.9 -> chunks returned")
        for number, (question, _) in enumerate(QUESTIONS, start=1):
            seen.append(f"   1.{number}: {sweep(conn, question)}")

        # 3. The same with the 2-word rule off: the same SQL with MIN_MATCHED_TERMS = 1.
        # `search` reads the module constant on every call, so patching it for this block runs the
        # service's own query with the rule off, without copying its SQL here; it is restored after.
        with mock.patch.object(retriever, "MIN_MATCHED_TERMS", 1):
            for number in (5, 6, 7):
                question, expected = QUESTIONS[number - 1]
                hits = postgres_search(conn, question)
                seen.append(f"3. rule off, 1.{number}: {sweep(conn, question)}")
                seen.append(f"   at 0.30: {verdict(hits, expected)} | {show(hits)}")

        # 4. RAG_TOP_K: how many sections the model gets for one question.
        for top_k in (1, 3, 5):
            hits = postgres_search(conn, QUESTIONS[0][0], top_k=top_k)
            seen.append(f"4. top_k {top_k}, 1.1: {show(hits)}")

        # 5. Chunking: words the model would get, heading sections against whole documents.
        doc_words = {s.document: 0 for s in sections}
        for section in sections:
            doc_words[section.document] += n_words(f"{section.heading} {section.content}")
        longest = max(n_words(s.content) for s in sections)
        seen.append(f"5. words per document: {doc_words}; longest chunk {longest}")
        for number, (question, _) in enumerate(QUESTIONS[:4], start=1):
            chunks = retriever.search(conn, question, min_score=MIN_SCORE, top_k=TOP_K)
            sent = sum(n_words(f"{c.heading} {c.content}") for c in chunks)
            whole = sum(doc_words[name] for name in {c.document for c in chunks})
            seen.append(f"   1.{number}: sections {sent} words, their whole documents {whole}")

    # 6. End to end: the real knowledge node, built as the service builds it, with FakeLLM.
    settings = Settings(
        llm_provider="fake", database_url=database_url, rag_min_score=MIN_SCORE, rag_top_k=TOP_K
    )
    pool = make_pool(settings)
    pool.open(wait=True, timeout=10)
    try:
        clock = FixedClock(datetime(2026, 11, 4, 8, 0, tzinfo=UTC))
        deps = WorkflowDeps(settings=settings, pool=pool, clock=clock, llm=FakeLLM())
        node = make_knowledge(deps)
        for number, (question, _) in enumerate(QUESTIONS, start=1):
            update = node(new_turn(question, f"t{number}"))
            lines = str(update["reply"]).splitlines()
            if update["outcome"] == "answered":
                summary = f"first line {lines[0]!r}, last line {lines[-1]!r}"
            else:
                summary = repr(update["reply"])
            seen.append(f"6.{number} {update['outcome']}: {summary}")
    finally:
        pool.close(timeout=5)
    return seen


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("Set DATABASE_URL to a PostgreSQL database (see the docstring).")
    for line in run(url):
        print(line)
