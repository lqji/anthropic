"""Finds the clinic document chunks that answer a question: PostgreSQL full-text search only.

The question's words (English stemming, stop words dropped) are combined with OR, so a chunk is
a candidate if it shares any of them. Candidates are ranked by `ts_rank_cd` with normalization 32
(rank / (rank + 1)), which keeps scores in [0, 1) so `RAG_MIN_SCORE` means the same for every
question. A chunk is returned only if it scores at least `min_score` and shares at least
`MIN_MATCHED_TERMS` of the question's words: one shared word ("test" in "Can you test my eyes?")
is not enough to answer from.

A chunk's searchable words are its heading, its text and its document's file name
("cancellation-policy.md" reads as "cancellation policy"), so "What is your cancellation policy?"
finds the chunks of that document.
"""

from dataclasses import dataclass
from typing import Final

from psycopg import Connection
from psycopg.rows import TupleRow

MIN_MATCHED_TERMS: Final = 2


@dataclass(frozen=True)
class Chunk:
    document: str  # file name, e.g. visit-preparation.md
    heading: str  # '' for text before the first heading
    content: str
    score: float
    matched_terms: int


def search(
    conn: Connection[TupleRow], question: str, *, min_score: float, top_k: int
) -> list[Chunk]:
    """At most `top_k` chunks that answer `question`, best first. [] if none qualifies.

    A question with no searchable word (only stop words or punctuation) gives [] without error:
    its query is NULL, and nothing matches NULL.
    """
    rows = conn.execute(
        r"""
        WITH q AS (
            SELECT array_agg(lexeme) AS lexemes,
                   to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
            FROM unnest(to_tsvector('english', %(question)s))
        ),
        scored AS (
            SELECT d.name AS document, c.heading, c.content, c.ordinal,
                   -- real -> float8, so the score Python sees is the one compared below
                   ts_rank_cd(v.tsv, q.query, 32)::float8 AS score,
                   cardinality(ARRAY(
                       SELECT unnest(tsvector_to_array(v.tsv))
                       INTERSECT
                       SELECT unnest(q.lexemes)
                   )) AS matched_terms
            FROM doc_chunks c
            JOIN documents d ON d.id = c.document_id
            CROSS JOIN q
            CROSS JOIN LATERAL (
                SELECT c.tsv || setweight(to_tsvector('english', regexp_replace(
                           regexp_replace(d.name, '\.md$', ''), '[-_]', ' ', 'g')), 'A') AS tsv
            ) v
            WHERE v.tsv @@ q.query
        )
        SELECT document, heading, content, score, matched_terms
        FROM scored
        WHERE score >= %(min_score)s AND matched_terms >= %(min_terms)s
        ORDER BY score DESC, document, ordinal
        LIMIT %(top_k)s
        """,
        {
            "question": question,
            "min_score": min_score,
            "min_terms": MIN_MATCHED_TERMS,
            "top_k": top_k,
        },
    ).fetchall()
    return [Chunk(*row) for row in rows]
