# R3: RAG

Issue: CFD-33. Owner: MrSn001. Experiment: `experiments/rag/`. Decision: `docs/adr/rag-design.md`.

## Question

How does the assistant find the part of the clinic's documents that answers a patient's question,
and how does it know when the documents do not answer it at all?

## What we need

The assistant may only answer clinic questions from the clinic's own four Markdown documents
(`seed/clinic_docs/`) and must name the document it used. If the documents do not cover the
question, it must say so rather than guess. So retrieval has two jobs, and the second matters more:
find the right section for a covered question, and find **nothing** for an uncovered one. A wrong
section sent to the model is worse than none, because the model then has text to build a
confident wrong answer from. PostgreSQL is the only database we may use, and CI has no API key.

## Options compared

| | A. Keyword overlap | B. PostgreSQL full-text search | C. Embeddings |
|---|---|---|---|
| How a chunk is scored | Share of the question's words found in it, exact words | `ts_rank_cd` over stemmed words, heading and file name weighted higher | Similarity between the question's vector and the chunk's vector |
| Where it runs | Python, over every chunk | One SQL query, GIN index on a generated `tsvector` column | An embedding API for every chunk and every question, then a vector search |
| "insurers" vs "insurance" | No match | Same stem (`insur`), match | Close vectors, match |
| Paraphrase ("health plan" for insurance) | No | No | Usually yes (not measured here) |
| Uncovered question | Any shared word is a hit | Nothing, if the threshold and the 2-word rule hold | Always has a nearest chunk; needs a similarity threshold |
| External call, key, cost | None | None | One API call per question and per chunk at load time |
| Testable in CI | Yes | Yes, against the CI Postgres | No: no key in CI |
| Fit with `AGENTS.md` | Allowed, but weak | Required ("RAG is Postgres full-text search") | Forbidden: needs a vector store (pgvector or similar) |

A and B were run on the same chunks. C was compared on paper. Running it would need an OpenAI key,
which CI does not have, and somewhere to keep the vectors. pgvector is a vector store inside
PostgreSQL, which the project rules forbid; keeping the vectors in a plain array column and comparing
them in Python would be the same thing built by hand. Its strength is real (paraphrases and synonyms),
but so is its weak point for us: a vector search always returns the closest chunk, even for laser eye
surgery, so "not covered" would rest on one similarity threshold and nothing else.

## Chunking choice

`rag/chunking.py` cuts each document at its Markdown headings, so a chunk is one section (for
example "Blood tests"). A section longer than 200 words is split between paragraphs, never inside one;
a title with no text under it (the `#` line) gives no chunk. Each chunk keeps its heading, and the
heading is weighted higher than the text in the search.

Why sections and not whole documents or fixed-size windows:

- The clinic writes one topic per heading, so a section is already a complete, readable answer.
- A fixed window of N words would cut sentences and mix two topics in one chunk.
- A whole document puts unrelated text in front of the model and makes the score depend on the
  document's length. The documents are small today (122 to 307 words), so the saving in words is
  modest (finding 7), but the answer can name the exact section it came from.
- The rule version is part of each file's fingerprint (`loader.fingerprint`), so changing the rules
  re-chunks every document on the next start.

## Sources

- PostgreSQL 16, full-text search introduction (documents, queries, `@@`):
  <https://www.postgresql.org/docs/16/textsearch-intro.html>
- PostgreSQL 16, parsing, `setweight` and ranking (`ts_rank_cd`, normalization 32):
  <https://www.postgresql.org/docs/16/textsearch-controls.html>
- PostgreSQL 16, text search functions (`to_tsvector`, `to_tsquery`, `tsvector_to_array`):
  <https://www.postgresql.org/docs/16/functions-textsearch.html>
- PostgreSQL 16, dictionaries (stop words, the Snowball English stemmer):
  <https://www.postgresql.org/docs/16/textsearch-dictionaries.html>
- PostgreSQL 16, GIN indexes for text search: <https://www.postgresql.org/docs/16/textsearch-indexes.html>
- PostgreSQL 16, generated columns (the stored `tsv` column):
  <https://www.postgresql.org/docs/16/ddl-generated-columns.html>
- OpenAI, vector embeddings (option C): <https://developers.openai.com/api/docs/guides/embeddings>
- pgvector, the PostgreSQL vector extension we may not use: <https://github.com/pgvector/pgvector>
- Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" (2020), where the
  term RAG comes from: <https://arxiv.org/abs/2005.11401>

Versions tested: PostgreSQL 16.15, psycopg 3.3.6. Every link above was opened on 2026-11-04.

## What the experiment showed

`experiments/rag/rag_demo.py` loads the four documents with the service's own `KnowledgeLoader`
(15 chunks) and asks seven questions: four covered ones, one about something the documents never
mention ("Do you offer laser eye surgery?"), one that shares a single word with them ("Can you test my
eyes?") and one covered question with a single searchable word ("What should I bring?"). Run it with
`DATABASE_URL=... uv run python experiments/rag/rag_demo.py`; the README there has the full output.

1. **Full-text search was right 6 times out of 7, keyword overlap 3.** B found the right document
   for the four covered questions and nothing for both uncovered ones. A answered both uncovered
   questions, from "offer" in the cancellation notice and from "test" in the visit preparation
   document, and picked the cancellation policy for "What are your opening hours?" because that text
   also says "opening hours" and the tie went to the first file. B's one miss is finding 4.
2. **Stemming and the file name help B.** "Which insurers do you accept?" matched the "Accepted
   insurance" heading (`insur`, `accept`), which A's exact words could not. For "What is your
   cancellation policy?" no section of the cancellation document says "policy"; they reach the 2
   shared words only because the retriever adds the file name (`cancellation-policy.md`) to every
   chunk, with the highest weight. Without it, every chunk would share at most one word and be dropped.
3. **The 2-word rule, not `RAG_MIN_SCORE`, keeps uncovered questions out.** With the rule on, every
   `min_score` from 0.1 to 0.5 returned the same chunks. With the rule off, "Can you test my eyes?"
   passed 0.30 and would have been answered from "Blood tests" (score 0.69).
4. **The rule has a price: a covered one-word question is not answered.** "What should I bring?"
   has only one searchable word ("bring"), so the rule drops it, although "What to bring" is the
   right section. With the rule off it scores 0.58: lower than the wrong "test my eyes" match. No
   threshold can keep the right one and drop the wrong one, so the service chooses "not covered"
   for both.
5. **`RAG_MIN_SCORE` has a narrow useful range.** Covered questions had best scores of 0.78 to 0.90.
   At 0.7 one wrong-document chunk disappeared; at 0.9 every question lost everything. 0.30 sits well
   below the good answers and is not what protects the uncovered ones (finding 3).
6. **`RAG_TOP_K` = 3 can let in a chunk from another document.** For the blood test question the
   second chunk was from the insurance document (it shares "need" and "test", score 0.55). The model
   still received the blood test section first. With `top_k` 1 only the blood test section was sent.
7. **Heading sections send less text.** For the blood test question the two sections had 254 words;
   the two whole documents would have had 473. For a question answered by one document the saving
   was small (127 against 127 for opening hours, 89 against 122 for cancellation).
8. **End to end, the node does what the contract says.** The real `knowledge` node built with
   `FakeLLM` answered the four covered questions with a `Source:` line naming the right document. For the
   other three nothing was retrieved, so it gave the fixed "not covered" reply; in that case the
   node does not call the model at all.

Findings 1 and 3 answer the question. Finding 4 is the trade-off we accept.

## When nothing relevant is found

If no chunk passes both checks, the `knowledge` node does not call the model. It returns the fixed
reply `NOT_IN_DOCUMENTS_REPLY`, `outcome: "not_in_documents"` and no sources. The same reply is
given when the model says it found nothing, or cites a document it was not given.

Real replies from the running demo stack (version 0.1.0, demo mode `LLM_PROVIDER=fake`), conversation
`2e34d5ff-d355-4147-bfe1-cb12f807e5f5` for patient `rag-demo`:

```text
patient: What is the price of laser eye surgery?
{"reply":"Sorry, that isn't covered in our clinic's documents, so I can't answer it. Please call the front desk during opening hours.","intent":"clinic_question","outcome":"not_in_documents","status":"active","sources":[]}

patient: How should I prepare for a blood test?
{"reply":"Blood tests\nSome blood tests require fasting. Whether your test needs fasting, and for how long, is set by the doctor who requested it.\n\nIf you are not sure whether you need to fast before your blood test, call the front desk before your visit and we will check the request from your doctor.\n\nSource: visit-preparation.md","intent":"clinic_question","outcome":"answered","status":"active","sources":[{"document":"visit-preparation.md","heading":"Blood tests"},{"document":"visit-preparation.md","heading":"What to bring"}]}
```

The fake model replies with the chosen section itself; a real model would write a short answer from
it, and the node would still add the `Source:` line. Before these two I asked "Do you offer laser eye
surgery?" and "Can you test my eyes?" on the same stack. Both got the clarifying question ("I'm not
sure what you need...", `outcome: "clarify"`): the fake classifier only sends a message to
`knowledge` when it contains a clinic word such as "price", "prepare" or "insurance". So in demo mode
retrieval is never reached for those wordings; with a real model it may be, and finding 1 shows
what retrieval then does.

## What the service does today

- `KnowledgeLoader` (`rag/loader.py`) loads every `*.md` file at startup into `documents` and
  `doc_chunks` (`sql/04_knowledge.sql`, SQL in `repositories/knowledge.py`), replacing a changed
  file's chunks in one transaction and removing documents whose file is gone or broken.
- `doc_chunks.tsv` is a stored generated column: the heading with weight A and the text with weight
  B, English configuration, with a GIN index.
- `retriever.search` turns the question into its English lexemes joined with OR, adds the document's
  file name to each chunk with weight A, ranks with `ts_rank_cd(..., 32)` so scores are between 0 and
  1, and keeps at most `RAG_TOP_K` chunks that score at least `RAG_MIN_SCORE` and share at least 2
  lexemes with the question (`MIN_MATCHED_TERMS`). All values are query parameters.
- The `knowledge` node (`workflows/nodes/knowledge.py`) calls the model only with those chunks, uses
  the answer only if it cites one of their documents, and ends it with `Source: <document>`.

## Recommendation

Keep PostgreSQL full-text search over heading sections, as the code does, with `RAG_MIN_SCORE=0.30`,
`RAG_TOP_K=3` and the 2-word rule. It was the only option that stayed quiet on both uncovered
questions, it runs in the database we already have, and CI tests it with no key. Keyword overlap
answered questions it should not have. Embeddings would handle paraphrases better but need an
external call per question and a vector store the rules forbid, and would still need a threshold to
say "not covered". ADR `rag-design` records this.

## Risks and known limitations

- **`RAG_MIN_SCORE` is not a safety net on its own.** The wrong one-word match scored 0.69, just
  under the weakest correct chunks (0.71 and 0.74) and above a correct one-word match (0.58). A
  threshold of 0.7 would block it today with a margin of 0.01, and one new document could change
  that; at 0.9 everything is lost. The 2-word rule is what keeps uncovered questions out, so anyone
  changing `MIN_MATCHED_TERMS` or `RAG_MIN_SCORE` should rerun the experiment.
- **Short covered questions are refused.** "What should I bring?" and "When do you close?" have one
  searchable word and get "not covered". The patient is sent to the front desk, which is safe but
  unhelpful. Rewording the question with two content words works.
- **`RAG_TOP_K` lets in neighbours.** With 3, a chunk from another document can reach the model
  (finding 6). The grounding guard only checks that the cited document was among those retrieved, so
  a real model could still answer from the wrong one. A lower `top_k` would send less noise but give
  the model less context for questions that span two sections (the insurance section is split in
  two).
- **Words, not meaning.** "Do you take my health plan?" finds nothing: each chunk shares at
  most one of its words ("health" or "take"), and no document says "plan". Paraphrases need the clinic's wording, or a later
  issue that adds synonyms (a PostgreSQL thesaurus dictionary) without leaving full-text search.
- **English only.** The English stemmer and stop words are used for documents and questions. A
  question in Arabic, likely in a clinic in Muscat, would match nothing and get "not covered". Not
  tested here.
- **The file name counts.** A renamed file changes what is found (finding 2). A file name should
  describe its topic.
- **Scores depend on the documents.** All numbers above are for these four documents. A new or longer
  document changes the ranking, so the experiment and `tests/test_rag_experiment.py` should be rerun
  when the documents change.
