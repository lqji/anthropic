"""SQL for the `documents` and `doc_chunks` tables (schema in sql/04_knowledge.sql)."""

from collections.abc import Sequence

from psycopg import Connection
from psycopg.rows import TupleRow


def get_document_sha(conn: Connection[TupleRow], name: str) -> str | None:
    """sha256 stored for the document file `name`, or None if it was never loaded."""
    row = conn.execute("SELECT sha256 FROM documents WHERE name = %s", (name,)).fetchone()
    return None if row is None else str(row[0])


def replace_document(
    conn: Connection[TupleRow],
    name: str,
    sha256: str,
    chunks: Sequence[tuple[str, str]],
) -> None:
    """Store a document and replace all its chunks, (heading, content) in order, atomically."""
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO documents (name, sha256) VALUES (%(name)s, %(sha256)s)
            ON CONFLICT (name) DO UPDATE SET sha256 = EXCLUDED.sha256
            RETURNING id
            """,
            {"name": name, "sha256": sha256},
        ).fetchone()
        assert row is not None  # noqa: S101 - INSERT ... RETURNING always returns a row
        document_id = row[0]
        conn.execute("DELETE FROM doc_chunks WHERE document_id = %s", (document_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO doc_chunks (document_id, ordinal, heading, content)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (document_id, ordinal, heading, content)
                    for ordinal, (heading, content) in enumerate(chunks)
                ],
            )
