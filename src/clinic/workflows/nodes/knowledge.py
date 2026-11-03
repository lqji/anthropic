import logging
from typing import Final

from clinic.llm import Passage
from clinic.rag import retriever
from clinic.rag.retriever import Chunk
from clinic.workflows.deps import WorkflowDeps
from clinic.workflows.nodes._stub import not_implemented
from clinic.workflows.nodes._types import Node
from clinic.workflows.routing import latest_patient_text
from clinic.workflows.state import ConversationState

logger = logging.getLogger("clinic.knowledge")

ANSWERED: Final = "answered"
NOT_IN_DOCUMENTS: Final = "not_in_documents"
NOT_IN_DOCUMENTS_REPLY: Final = (
    "Sorry, that isn't covered in our clinic's documents, so I can't answer it. "
    "Please call the front desk during opening hours."
)


def knowledge(state: ConversationState) -> dict[str, object]:
    """Stub used when the graph is built without dependencies (routing-only tests)."""
    return not_implemented()


def make_knowledge(deps: WorkflowDeps) -> Node:
    """Build the real `knowledge` node: answer only from the clinic documents, naming the source.

    The chunks come from PostgreSQL full-text search (`rag.retriever`). If none qualifies, the
    model is not called. The model answers from those chunks only; its answer is used only if it
    has text and cites one of the documents it was given, otherwise it counts as not found.
    """

    def knowledge(state: ConversationState) -> dict[str, object]:
        question = latest_patient_text(state)
        with deps.pool.connection() as conn:  # released before the model call
            chunks = retriever.search(
                conn,
                question,
                min_score=deps.settings.rag_min_score,
                top_k=deps.settings.rag_top_k,
            )
        if not chunks:
            return _not_in_documents()

        result = deps.llm.answer(question, [_passage(chunk) for chunk in chunks])
        retrieved = {chunk.document for chunk in chunks}
        if not result.found or not result.text.strip():
            return _not_in_documents()
        if result.source not in retrieved:
            # Grounding guard: an answer that cites a document it was not given is not grounded.
            logger.warning("knowledge.ungrounded_source", extra={"fields": {"chunks": len(chunks)}})
            return _not_in_documents()

        sources = [
            {"document": chunk.document, "heading": chunk.heading}
            for chunk in chunks
            if chunk.document == result.source
        ]
        return {
            "reply": f"{result.text.strip()}\n\nSource: {result.source}",
            "outcome": ANSWERED,
            "sources": sources,
        }

    return knowledge


def _passage(chunk: Chunk) -> Passage:
    """The model sees the heading with the text, and the document name as the source to cite."""
    text = f"{chunk.heading}\n{chunk.content}" if chunk.heading else chunk.content
    return Passage(source=chunk.document, text=text)


def _not_in_documents() -> dict[str, object]:
    return {"reply": NOT_IN_DOCUMENTS_REPLY, "outcome": NOT_IN_DOCUMENTS, "sources": []}
