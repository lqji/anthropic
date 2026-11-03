import logging
import shutil
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path

import psycopg
import pytest
from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.rag.chunking import chunk_document
from clinic.rag.loader import KnowledgeLoader
from clinic.repositories import knowledge

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "clinic_docs"
SEED_NAMES = [
    "cancellation-policy.md",
    "insurance.md",
    "opening-hours.md",
    "visit-preparation.md",
]


@pytest.fixture
def conn(database_url: str) -> Iterator[Connection[TupleRow]]:
    """Autocommit connection with the knowledge schema applied and its tables emptied."""
    schema = files("clinic").joinpath("sql/04_knowledge.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url, autocommit=True, connect_timeout=5) as connection:
        connection.execute(schema)
        connection.execute("TRUNCATE documents, doc_chunks RESTART IDENTITY")
        yield connection


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """A writable copy of the seed documents."""
    target = tmp_path / "clinic_docs"
    shutil.copytree(SEED_DIR, target)
    return target


def _row_versions(conn: Connection[TupleRow]) -> list[tuple[str, int, str]]:
    """(table, id, xmin) of every row: xmin changes whenever a row is inserted or updated."""
    return conn.execute(
        """
        SELECT 'documents', id, xmin::text FROM documents
        UNION ALL
        SELECT 'doc_chunks', id, xmin::text FROM doc_chunks
        ORDER BY 1, 2
        """
    ).fetchall()


def _chunks_of(conn: Connection[TupleRow], name: str) -> list[tuple[str, str]]:
    return conn.execute(
        """
        SELECT c.heading, c.content
        FROM doc_chunks c JOIN documents d ON d.id = c.document_id
        WHERE d.name = %s
        ORDER BY c.ordinal
        """,
        (name,),
    ).fetchall()


def test_empty_db_loads_four_documents_one_row_per_chunk(
    conn: Connection[TupleRow], docs_dir: Path
) -> None:
    report = KnowledgeLoader(docs_dir).load(conn)

    assert report.loaded == SEED_NAMES
    assert report.skipped == []
    names = [r[0] for r in conn.execute("SELECT name FROM documents ORDER BY name").fetchall()]
    assert names == SEED_NAMES
    for name in SEED_NAMES:
        expected = chunk_document((docs_dir / name).read_text(encoding="utf-8"))
        assert _chunks_of(conn, name) == [(c.heading, c.content) for c in expected]
    # The long "Accepted insurance" section was split into two rows under the same heading.
    headings = [h for h, _ in _chunks_of(conn, "insurance.md")]
    assert headings.count("Accepted insurance") == 2


def test_chunks_are_full_text_searchable(conn: Connection[TupleRow], docs_dir: Path) -> None:
    KnowledgeLoader(docs_dir).load(conn)

    row = conn.execute(
        """
        SELECT d.name, c.heading
        FROM doc_chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.tsv @@ plainto_tsquery('english', 'fasting blood test')
        """
    ).fetchone()
    assert row == ("visit-preparation.md", "Blood tests")


def test_restart_with_unchanged_files_changes_no_rows(
    conn: Connection[TupleRow], docs_dir: Path
) -> None:
    KnowledgeLoader(docs_dir).load(conn)
    before = _row_versions(conn)

    report = KnowledgeLoader(docs_dir).load(conn)

    assert report.unchanged == SEED_NAMES
    assert report.loaded == []
    assert _row_versions(conn) == before


def _other_documents_rows(conn: Connection[TupleRow], name: str) -> list[tuple[int, str]]:
    """(id, xmin) of every documents and doc_chunks row that does not belong to `name`."""
    return conn.execute(
        """
        SELECT d.id, d.xmin::text FROM documents d WHERE d.name <> %(name)s
        UNION ALL
        SELECT c.id, c.xmin::text
        FROM doc_chunks c JOIN documents d ON d.id = c.document_id
        WHERE d.name <> %(name)s
        ORDER BY 1, 2
        """,
        {"name": name},
    ).fetchall()


def test_changed_file_replaces_only_its_chunks(conn: Connection[TupleRow], docs_dir: Path) -> None:
    KnowledgeLoader(docs_dir).load(conn)
    others_before = _other_documents_rows(conn, "opening-hours.md")
    (docs_dir / "opening-hours.md").write_text(
        "# Opening hours\n\n## Daily hours\n\nOpen 08:00 to 20:00.\n", encoding="utf-8"
    )

    report = KnowledgeLoader(docs_dir).load(conn)

    assert report.loaded == ["opening-hours.md"]
    assert report.unchanged == [n for n in SEED_NAMES if n != "opening-hours.md"]
    assert _chunks_of(conn, "opening-hours.md") == [("Daily hours", "Open 08:00 to 20:00.")]
    assert _other_documents_rows(conn, "opening-hours.md") == others_before


def test_failed_replace_keeps_old_chunks(conn: Connection[TupleRow]) -> None:
    knowledge.replace_document(conn, "a.md", "a" * 64, [("Old", "old text")])

    with pytest.raises(psycopg.DataError):
        # The second chunk is rejected by PostgreSQL after the old chunks were deleted.
        knowledge.replace_document(conn, "a.md", "b" * 64, [("New", "new"), ("Bad", "nul\x00")])

    assert _chunks_of(conn, "a.md") == [("Old", "old text")]
    assert knowledge.get_document_sha(conn, "a.md") == "a" * 64


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"", "empty"),
        (b"  \n\n\t\n", "empty"),
        (b"# Hours\n\nOpen \xff\xfe daily.\n", "not valid UTF-8"),
        (b"# Hours\n\nOpen\x00 daily.\n", "NUL"),
        (b"Plain text without any heading.\n", "no Markdown heading"),
    ],
)
def test_bad_file_is_logged_and_skipped_others_still_load(
    conn: Connection[TupleRow],
    docs_dir: Path,
    caplog: pytest.LogCaptureFixture,
    content: bytes,
    reason: str,
) -> None:
    (docs_dir / "broken.md").write_bytes(content)

    with caplog.at_level(logging.ERROR, logger="clinic.rag"):
        report = KnowledgeLoader(docs_dir).load(conn)

    assert report.skipped == ["broken.md"]
    assert report.loaded == SEED_NAMES
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "broken.md" in errors[0].getMessage()
    assert reason in errors[0].getMessage()
    assert knowledge.get_document_sha(conn, "broken.md") is None


def test_utf8_bom_is_accepted(conn: Connection[TupleRow], tmp_path: Path) -> None:
    (tmp_path / "bom.md").write_bytes("﻿# Hours\n\nOpen daily.\n".encode())

    KnowledgeLoader(tmp_path).load(conn)

    assert _chunks_of(conn, "bom.md") == [("Hours", "Open daily.")]


def test_missing_directory_fails_fast(conn: Connection[TupleRow], tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="nope"):
        KnowledgeLoader(tmp_path / "nope").load(conn)
