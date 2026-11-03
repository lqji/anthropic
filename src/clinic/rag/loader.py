"""Load the clinic's Markdown documents into `documents` / `doc_chunks` (run once at startup)."""

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from psycopg import Connection
from psycopg.rows import TupleRow

from clinic.rag.chunking import CHUNKING_VERSION, MAX_CHUNK_WORDS, ChunkingError, chunk_document
from clinic.repositories import knowledge

logger = logging.getLogger("clinic.rag")


@dataclass
class LoadReport:
    loaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


class KnowledgeLoader:
    """Loads every `*.md` file in `docs_dir`, so the database matches the good files on disk.

    A file whose fingerprint matches the stored one is left alone. A new or changed file has all
    its chunks replaced in one transaction. A file that cannot be chunked is logged and skipped,
    so one bad file never stops the others. Documents of skipped or deleted files are removed:
    answering "not covered" is safer than answering from text the clinic no longer publishes.
    Database errors propagate.
    """

    def __init__(self, docs_dir: Path) -> None:
        self._docs_dir = docs_dir

    def load(self, conn: Connection[TupleRow]) -> LoadReport:
        if not self._docs_dir.is_dir():
            raise FileNotFoundError(f"clinic documents directory not found: {self._docs_dir}")

        report = LoadReport()
        for path in sorted(self._docs_dir.glob("*.md")):
            name = path.name
            raw = path.read_bytes()
            sha256 = fingerprint(raw)
            if knowledge.get_document_sha(conn, name) == sha256:
                report.unchanged.append(name)
                continue
            try:
                chunks = chunk_document(_decode(raw))
            except ChunkingError as exc:
                logger.error(
                    "knowledge.file_skipped: %s: %s",
                    name,
                    exc,
                    extra={"fields": {"file": name, "reason": str(exc)}},
                )
                report.skipped.append(name)
                continue
            knowledge.replace_document(
                conn, name, sha256, [(chunk.heading, chunk.content) for chunk in chunks]
            )
            report.loaded.append(name)

        report.removed = knowledge.delete_documents_except(conn, report.loaded + report.unchanged)
        for name in report.removed:
            logger.warning("knowledge.document_removed: %s", name, extra={"fields": {"file": name}})

        logger.info(
            "knowledge.loaded",
            extra={
                "fields": {
                    "loaded": len(report.loaded),
                    "unchanged": len(report.unchanged),
                    "skipped": len(report.skipped),
                    "removed": len(report.removed),
                }
            },
        )
        return report


def fingerprint(raw: bytes) -> str:
    """sha256 of the file bytes and the chunking rules: either changing re-chunks the file."""
    rules = f"chunking-v{CHUNKING_VERSION}:max-words-{MAX_CHUNK_WORDS}\n".encode()
    return hashlib.sha256(rules + raw).hexdigest()


def _decode(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8-sig")  # tolerate a byte-order mark
    except UnicodeDecodeError as exc:
        raise ChunkingError(f"file is not valid UTF-8 ({exc.reason} at byte {exc.start})") from exc
    if "\x00" in text:
        # PostgreSQL text cannot store NUL; treat it as a broken file, not a database failure.
        raise ChunkingError("file contains a NUL character")
    return text
