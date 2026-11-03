"""Split a Markdown clinic document into heading sections, and long sections into smaller chunks.

Pure functions, no I/O. The loader turns the chunks into `doc_chunks` rows.
"""

import re
from dataclasses import dataclass

MAX_CHUNK_WORDS = 200

_HEADING = re.compile(r"^#{1,6}\s+(.+?)(?:\s+#+)?$")


class ChunkingError(ValueError):
    """The document cannot be chunked (empty, or no Markdown heading)."""


@dataclass(frozen=True)
class Chunk:
    heading: str
    content: str


def chunk_document(text: str, max_words: int = MAX_CHUNK_WORDS) -> list[Chunk]:
    """One chunk per heading section; a section over `max_words` is split on paragraph boundaries.

    A heading with no text under it (e.g. a title directly followed by a sub-heading) gives no
    chunk. Text before the first heading becomes a chunk with an empty heading.
    """
    lines = [_normalise_line(line) for line in text.splitlines()]
    if not any(lines):
        raise ChunkingError("document is empty")

    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        match = _HEADING.match(line)
        if match:
            sections.append((match.group(1), []))
        else:
            sections[-1][1].append(line)
    if len(sections) == 1:
        raise ChunkingError("document has no Markdown heading")

    chunks: list[Chunk] = []
    for heading, body in sections:
        for content in _split_long(_paragraphs(body), max_words):
            chunks.append(Chunk(heading=heading, content=content))
    if not chunks:
        raise ChunkingError("document has headings but no text")
    return chunks


def _normalise_line(line: str) -> str:
    """Collapse runs of whitespace (tabs, non-breaking spaces, ...) to one space and trim."""
    return " ".join(line.split())


def _paragraphs(lines: list[str]) -> list[str]:
    """Group lines into paragraphs separated by one or more blank lines."""
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            paragraphs.append("\n".join(current))
            current = []
    if current:
        paragraphs.append("\n".join(current))
    return paragraphs


def _split_long(paragraphs: list[str], max_words: int) -> list[str]:
    """Pack whole paragraphs into chunks of at most `max_words` words.

    A single paragraph longer than `max_words` is kept whole rather than cut mid-sentence.
    """
    chunks: list[str] = []
    current: list[str] = []
    words = 0
    for paragraph in paragraphs:
        paragraph_words = len(paragraph.split())
        if current and words + paragraph_words > max_words:
            chunks.append("\n\n".join(current))
            current, words = [], 0
        current.append(paragraph)
        words += paragraph_words
    if current:
        chunks.append("\n\n".join(current))
    return chunks
