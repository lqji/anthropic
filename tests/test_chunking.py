from pathlib import Path

import pytest

from clinic.rag.chunking import MAX_CHUNK_WORDS, Chunk, ChunkingError, chunk_document

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "clinic_docs"


def _paragraph(word: str, count: int) -> str:
    return " ".join([word] * count)


def test_one_chunk_per_heading_section() -> None:
    text = (
        "# Opening hours\n\n"
        "## Daily\n\nOpen 09:00 to 13:00.\n\n"
        "## Lunch\n\nClosed 13:00 to 14:00.\n"
    )
    assert chunk_document(text) == [
        Chunk(heading="Daily", content="Open 09:00 to 13:00."),
        Chunk(heading="Lunch", content="Closed 13:00 to 14:00."),
    ]


def test_heading_levels_and_closing_hashes() -> None:
    text = "### Fees ###\nCash or card.\n###### C# course\nNot offered."
    assert [c.heading for c in chunk_document(text)] == ["Fees", "C# course"]


def test_hash_without_space_is_not_a_heading() -> None:
    text = "# Notes\n#hashtag stays in the body"
    assert chunk_document(text) == [Chunk(heading="Notes", content="#hashtag stays in the body")]


def test_text_before_first_heading_gets_empty_heading() -> None:
    text = "Welcome to the clinic.\n\n# Hours\nOpen daily."
    assert chunk_document(text) == [
        Chunk(heading="", content="Welcome to the clinic."),
        Chunk(heading="Hours", content="Open daily."),
    ]


def test_long_section_splits_on_paragraph_boundaries() -> None:
    paragraphs = [_paragraph(w, 80) for w in ("alpha", "beta", "gamma", "delta")]
    text = "# Long\n\n" + "\n\n".join(paragraphs)

    chunks = chunk_document(text)

    # 80 + 80 fits in 200, adding a third would not.
    assert [c.content for c in chunks] == [
        paragraphs[0] + "\n\n" + paragraphs[1],
        paragraphs[2] + "\n\n" + paragraphs[3],
    ]
    assert all(c.heading == "Long" for c in chunks)
    assert all(len(c.content.split()) <= MAX_CHUNK_WORDS for c in chunks)


def test_section_of_exactly_max_words_is_not_split() -> None:
    text = "# Edge\n\n" + _paragraph("a", 100) + "\n\n" + _paragraph("b", 100)
    assert len(chunk_document(text)) == 1


def test_single_oversized_paragraph_is_kept_whole() -> None:
    text = "# Big\n\n" + _paragraph("word", 250) + "\n\nshort tail"
    assert [len(c.content.split()) for c in chunk_document(text)] == [250, 2]


def test_whitespace_is_normalised() -> None:
    text = (
        "#   Visit\t prep  \r\n\r\n\r\n"
        "  Bring your   ID.\t\r\n"
        "-  Insurance   card  \n\n\n\n"
        "Arrive early.  "
    )
    assert chunk_document(text) == [
        Chunk(heading="Visit prep", content="Bring your ID.\n- Insurance card\n\nArrive early.")
    ]


@pytest.mark.parametrize("text", ["", "   \n\t\n  "])
def test_empty_document_is_rejected(text: str) -> None:
    with pytest.raises(ChunkingError, match="empty"):
        chunk_document(text)


def test_document_without_heading_is_rejected() -> None:
    with pytest.raises(ChunkingError, match="no Markdown heading"):
        chunk_document("Just some text.\n\nNo headings here.")


def test_document_with_only_headings_is_rejected() -> None:
    with pytest.raises(ChunkingError, match="no text"):
        chunk_document("# Title\n\n## Empty section\n")


def test_seed_documents_chunk_cleanly() -> None:
    files = sorted(SEED_DIR.glob("*.md"))
    assert [f.name for f in files] == [
        "cancellation-policy.md",
        "insurance.md",
        "new-patients.md",
        "opening-hours.md",
        "payment-and-fees.md",
        "visit-preparation.md",
    ]
    for path in files:
        chunks = chunk_document(path.read_text(encoding="utf-8"))
        assert chunks, path.name
        assert all(c.heading for c in chunks), path.name


def test_seed_insurance_long_section_is_split() -> None:
    chunks = chunk_document((SEED_DIR / "insurance.md").read_text(encoding="utf-8"))
    accepted = [c for c in chunks if c.heading == "Accepted insurance"]
    assert len(accepted) == 2
