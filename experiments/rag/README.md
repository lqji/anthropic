# Experiment: retrieval (R3)

Which way of finding clinic text answers the R3 question best: find the right document for a
clinic question, and find nothing for a question the documents do not cover? It uses the four
clinic documents in `seed/clinic_docs/`, the service's own loader and retriever, and the project's
`FakeLLM` (no API key, no external call).

Two options search the same 15 chunks:

- **A. Keyword overlap**, written in the experiment: a chunk scores the share of the question's
  words (minus a short stop-word list) that appear in its heading or text. Exact words only.
- **B. PostgreSQL full-text search**, the service's `clinic.rag.retriever.search`, unchanged.

Embeddings are not run: they need an external API key that CI does not have, and storing vectors
needs a vector store, which the project rules forbid. `docs/research/R3-rag.md` compares them on
paper.

## Run

It needs a PostgreSQL. The dev database works (`docker compose -f compose.yaml -f compose.dev.yaml
up -d db`):

```bash
DATABASE_URL=postgresql://clinic:change-me-local-only@localhost:5432/clinic \
  uv run python experiments/rag/rag_demo.py
```

It creates the app's tables with `apply_schema` and loads the documents with `KnowledgeLoader`,
as the service does at startup. Both are idempotent, so it can run again on the same database
(and on the dev database of a running app, where it changes nothing). Its output is the same on
every run. `tests/test_rag_experiment.py` runs it in CI against a fresh database, so it cannot go
stale.

## What the experiment prints

```text
0. 15 chunks: cancellation-policy.md=4, insurance.md=4, opening-hours.md=3, visit-preparation.md=4
1.1 'Do I need to fast before a blood test?' expected: visit-preparation.md
     A keyword:  right | visit-preparation.md / Blood tests 1.00 (3w); visit-preparation.md / What to bring 0.33 (1w)
     B postgres: right | visit-preparation.md / Blood tests 0.86 (4w); insurance.md / Accepted insurance 0.55 (2w)
1.2 'What are your opening hours?' expected: opening-hours.md
     A keyword:  WRONG: cancellation-policy.md | cancellation-policy.md / How to cancel 1.00 (2w); opening-hours.md / Front desk and phone 1.00 (2w); opening-hours.md / Daily hours 0.50 (1w)
     B postgres: right | opening-hours.md / Front desk and phone 0.78 (2w); opening-hours.md / Daily hours 0.77 (2w); opening-hours.md / Appointments and walk-ins 0.74 (2w)
1.3 'Which insurers do you accept?' expected: insurance.md
     A keyword:  right | insurance.md / Paying without insurance 0.50 (1w)
     B postgres: right | insurance.md / Accepted insurance 0.90 (2w); insurance.md / Accepted insurance 0.79 (2w); insurance.md / Paying without insurance 0.74 (2w)
1.4 'What is your cancellation policy?' expected: cancellation-policy.md
     A keyword:  right | cancellation-policy.md / How to cancel 0.50 (1w); insurance.md / Accepted insurance 0.50 (1w); insurance.md / Changes to your insurance 0.50 (1w)
     B postgres: right | cancellation-policy.md / How to cancel 0.81 (2w); cancellation-policy.md / Notice 0.71 (2w); cancellation-policy.md / Changing an appointment 0.71 (2w)
1.5 'Do you offer laser eye surgery?' expected: not covered
     A keyword:  WRONG: would answer from cancellation-policy.md | cancellation-policy.md / Notice 0.25 (1w)
     B postgres: right (nothing found) | -
1.6 'Can you test my eyes?' expected: not covered
     A keyword:  WRONG: would answer from visit-preparation.md | visit-preparation.md / What to bring 0.50 (1w); visit-preparation.md / Blood tests 0.50 (1w)
     B postgres: right (nothing found) | -
1.7 'What should I bring?' expected: visit-preparation.md
     A keyword:  WRONG: insurance.md | insurance.md / Changes to your insurance 0.50 (1w); visit-preparation.md / What to bring 0.50 (1w); visit-preparation.md / Children and companions 0.50 (1w)
     B postgres: MISSED | -
   right: A keyword 3/7, B postgres 6/7
   A keyword with the 2-word rule: 3/7
2. min_score 0.1 / 0.3 / 0.5 / 0.7 / 0.9 -> chunks returned
   1.1: 2 / 2 / 2 / 1 / 0
   1.2: 3 / 3 / 3 / 3 / 0
   1.3: 3 / 3 / 3 / 3 / 0
   1.4: 3 / 3 / 3 / 3 / 0
   1.5: 0 / 0 / 0 / 0 / 0
   1.6: 0 / 0 / 0 / 0 / 0
   1.7: 0 / 0 / 0 / 0 / 0
3. rule off, 1.5: 1 / 0 / 0 / 0 / 0
   at 0.30: right (nothing found) | -
3. rule off, 1.6: 3 / 1 / 1 / 0 / 0
   at 0.30: WRONG: would answer from visit-preparation.md | visit-preparation.md / Blood tests 0.69 (1w)
3. rule off, 1.7: 3 / 1 / 1 / 0 / 0
   at 0.30: right | visit-preparation.md / What to bring 0.58 (1w)
4. top_k 1, 1.1: visit-preparation.md / Blood tests 0.86 (4w)
4. top_k 3, 1.1: visit-preparation.md / Blood tests 0.86 (4w); insurance.md / Accepted insurance 0.55 (2w)
4. top_k 5, 1.1: visit-preparation.md / Blood tests 0.86 (4w); insurance.md / Accepted insurance 0.55 (2w)
5. words per document: {'cancellation-policy.md': 122, 'insurance.md': 307, 'opening-hours.md': 127, 'visit-preparation.md': 166}; longest chunk 198
   1.1: sections 254 words, their whole documents 473
   1.2: sections 127 words, their whole documents 127
   1.3: sections 282 words, their whole documents 307
   1.4: sections 89 words, their whole documents 122
6.1 answered: first line 'Blood tests', last line 'Source: visit-preparation.md'
6.2 answered: first line 'Front desk and phone', last line 'Source: opening-hours.md'
6.3 answered: first line 'Paying without insurance', last line 'Source: insurance.md'
6.4 answered: first line 'How to cancel', last line 'Source: cancellation-policy.md'
6.5 not_in_documents: "Sorry, that isn't covered in our clinic's documents, so I can't answer it. Please call the front desk during opening hours."
6.6 not_in_documents: "Sorry, that isn't covered in our clinic's documents, so I can't answer it. Please call the front desk during opening hours."
6.7 not_in_documents: "Sorry, that isn't covered in our clinic's documents, so I can't answer it. Please call the front desk during opening hours."
```

A hit reads `document / heading score (Nw)`, where `Nw` is how many of the question's words the
chunk shares. A is judged on its best hit; it has no threshold, so any shared word is a hit.

## How to read it

0. **What was loaded.** The real loader made 15 chunks, one per heading section. The long
   "Accepted insurance" section (over 200 words) was split into two chunks with the same heading.
1. **A against B.** B finds the right document for every covered question with two or more
   searchable words, and nothing for both uncovered ones. A answers both uncovered questions from
   one shared word ("offer", "test"), and picks the wrong document for "opening hours" because
   "opening hours" also appears in the cancellation text and the tie goes to the first file. A has
   no stemming: "insurers" matches nothing and "accept" does not match "Accepted insurance", so A
   only lands on the right document through "We accept cash". Giving A the 2-word rule does not
   help: it trades the wrong answers for misses (still 3/7).
2. **`RAG_MIN_SCORE` barely matters with the 2-word rule on.** Nothing changes between 0.1 and 0.5.
   At 0.7 the blood-test question loses its second (wrong-document) chunk; at 0.9 every question
   loses everything.
3. **The 2-word rule does the real work.** With the rule off, "Can you test my eyes?" passes 0.30
   on the single word "test" and would be answered from "Blood tests" (0.69). "What should I
   bring?" also has a single searchable word and is covered, but scores lower (0.58). No
   `min_score` keeps the right one and drops the wrong one. The rule drops both: it prefers
   "not covered" over a wrong answer.
4. **`RAG_TOP_K`.** With 3, the blood-test question also sends an insurance chunk to the model
   (it shares "need" and "test"). With 1 it does not. Only two chunks qualify, so 5 changes
   nothing.
5. **Chunking.** The documents are small (122 to 307 words), so heading sections save little for
   one-document questions. They save most when a stray chunk from another document qualifies
   (1.1: 254 words instead of 473), and they let the answer name the section it came from.
6. **End to end.** The real `knowledge` node answers the four covered questions with a
   `Source:` line and gives the fixed "not covered" reply for the other three without calling the
   model. For 1.3 `FakeLLM` picks "Paying without insurance": it matches exact words ("accept"),
   so this choice says something about the fake model, not about retrieval.
