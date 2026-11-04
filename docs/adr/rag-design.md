# ADR: Clinic answers are retrieved with PostgreSQL full-text search over heading sections

- Status: accepted
- Issue: CFD-33 (describes what CFD-10 and CFD-11 built)
- Research: `docs/research/R3-rag.md`, experiment `experiments/rag/`

## Context

The assistant answers clinic questions only from the clinic's Markdown documents and must name the
document it used. When the documents do not cover a question it must say so instead of guessing.
PostgreSQL is the project's only database, and the rules forbid a vector store. CI has no model API
key, so retrieval has to work and be tested without one.

## Decision

1. **PostgreSQL full-text search, no vector store.** Retrieval is one parameterised SQL query
   (`rag/retriever.py`). No embeddings, no pgvector, no second store to load, back up or keep in
   sync with the documents.
2. **Heading sections as chunks.** `rag/chunking.py` makes one chunk per Markdown heading section
   and splits a section over 200 words between paragraphs. Each chunk keeps its heading. The rule
   version is part of each file's fingerprint, so a rule change re-chunks every document.
3. **Loaded at startup, stored in two tables.** `KnowledgeLoader` (`rag/loader.py`) writes
   `documents` and `doc_chunks` (`sql/04_knowledge.sql`) through `repositories/knowledge.py`. A
   changed file has all its chunks replaced in one transaction; a broken or deleted file's document
   is removed, so the assistant never answers from text the clinic no longer publishes.
4. **What is searched.** `doc_chunks.tsv` is a stored generated column: heading with weight A, text
   with weight B, English configuration, GIN index. At query time the document's file name is added
   with weight A, so a question that names the document's topic finds its sections.
5. **How a chunk qualifies.** The question's English lexemes are joined with OR. Chunks are ranked
   by `ts_rank_cd` with normalization 32, so scores are between 0 and 1. A chunk is used only if it
   scores at least `RAG_MIN_SCORE` (0.30) **and** shares at least 2 lexemes with the question
   (`MIN_MATCHED_TERMS`). At most `RAG_TOP_K` (3) chunks are kept, best first.
6. **No chunk, no model call.** The `knowledge` node (`workflows/nodes/knowledge.py`) gives the
   fixed `NOT_IN_DOCUMENTS_REPLY` when nothing qualifies. Otherwise the model gets only those
   chunks; its answer is used only if it cites one of their documents, and the reply ends with
   `Source: <document>`.

## Consequences

- Retrieval runs inside the database we already operate and back up, and the same code is tested
  in CI against PostgreSQL with `FakeLLM`.
- In the experiment it found the right document for every covered question with two content words
  and nothing for both uncovered questions; keyword overlap answered both uncovered ones.
- A covered question with a single content word ("What should I bring?") gets "not covered": the
  2-word rule prefers sending the patient to the front desk over a possibly wrong answer.
- Matching is by stemmed English words. Paraphrases and questions in other languages are not found.
- `RAG_MIN_SCORE` alone does not separate uncovered questions from covered ones; the 2-word rule
  does. Changing either value, or the documents, means rerunning `experiments/rag/rag_demo.py`.
- With `RAG_TOP_K=3` a chunk from another document can reach the model next to the right one.

## Alternatives considered

- **Keyword overlap in Python.** Rejected: in the experiment it answered both uncovered questions
  from one shared word and picked the wrong document for "opening hours". It has no stemming.
- **Embeddings.** Rejected: an external API call for every question and every chunk, a key CI does
  not have, and a vector store the rules forbid. A nearest-neighbour search always returns
  something, so "not covered" would rest on one similarity threshold.
- **Whole documents as chunks.** Rejected: more unrelated text for the model and no section to
  name. With today's small documents the difference in words is modest, so this is the closest
  alternative.
