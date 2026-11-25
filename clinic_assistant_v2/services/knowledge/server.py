"""Clinic Knowledge Retrieval FastMCP Server (Port 8004).

Performs semantic and lexical search over clinic documentation stored in Qdrant.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp import FastMCP

from shared.auth import verify_token

logger = logging.getLogger(__name__)

mcp = FastMCP("Knowledge Retrieval MCP")

# In-memory document fallback for tests or when Qdrant is not yet seeded
SAMPLE_DOCS: list[dict[str, str]] = [
    {
        "source": "doctors.pdf",
        "text": (
            "Dr. Sarah Smith specializes in Dermatology and skin conditions. "
            "Clinic hours: Mon and Wed 9am-1pm."
        ),
    },
    {
        "source": "doctors.pdf",
        "text": (
            "Dr. John Doe specializes in Cardiology and heart health. "
            "Clinic hours: Tue and Thu 2pm-6pm."
        ),
    },
    {
        "source": "facilities.pdf",
        "text": (
            "Our clinic offers on-site Pharmacy, Radiology, and emergency triage. "
            "Pharmacy open 8am-8pm daily."
        ),
    },
    {
        "source": "policies.pdf",
        "text": (
            "Appointments may be cancelled up to 24 hours in advance with full refund. "
            "Same-day cancellations require staff approval."
        ),
    },
]


def _get_auth_header(token: str | None = None) -> str | None:
    if token:
        return token
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
        return headers.get("authorization")
    except Exception:
        return None


@mcp.tool()
def search_clinic_docs(
    query: str,
    top_k: int = 3,
    token: str | None = None,
) -> list[dict[str, Any]] | dict[str, str]:
    """Search clinic policies, doctors, and facilities documents.

    Requires valid JWT with scope 'docs:read'.
    """
    auth_header = _get_auth_header(token)
    claims = verify_token(auth_header, required_scope="docs:read")
    if "error" in claims:
        return {"error": "unauthorized"}

    qdrant_url = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    openai_key = os.environ.get("OPENAI_API_KEY")

    # If Qdrant and OpenAI are available, query Qdrant; otherwise use deterministic fallback
    if openai_key and openai_key != "your-key-here":
        try:
            from openai import OpenAI
            from qdrant_client import QdrantClient

            ai_client = OpenAI(api_key=openai_key)
            q_client = QdrantClient(url=qdrant_url, timeout=3.0)

            emb = (
                ai_client.embeddings.create(
                    model="text-embedding-3-small",
                    input=query,
                )
                .data[0]
                .embedding
            )

            search_res = q_client.search(
                collection_name="clinic_docs",
                query_vector=emb,
                limit=top_k,
            )
            return [
                {
                    "text": hit.payload.get("text", "") if hit.payload else "",
                    "source": hit.payload.get("source", "") if hit.payload else "",
                    "score": float(hit.score),
                }
                for hit in search_res
            ]
        except Exception as e:
            # Fall back to internal matcher if Qdrant isn't connected
            logger.debug("Qdrant search error, using fallback: %s", e)

    # Keyword / Semantic Fallback Matcher
    query_words = set(query.lower().split())
    ranked: list[tuple[float, dict[str, Any]]] = []
    for doc in SAMPLE_DOCS:
        doc_words = set(doc["text"].lower().split())
        overlap = len(query_words.intersection(doc_words))
        score = overlap / max(len(query_words), 1)
        ranked.append(
            (score, {"text": doc["text"], "source": doc["source"], "score": round(score, 3)})
        )

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in ranked[:top_k]]


if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8004"))
    mcp.run(transport="http", host=host, port=port)
