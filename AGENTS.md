# AGENTS.md

Clinic Front-Desk Assistant: FastAPI + LangGraph + PostgreSQL REST API. Read README.md first.

## Layers (calls go one way)
`api` (routers) -> `services` -> `workflows` (LangGraph) -> `repositories` (SQL) -> PostgreSQL. `domain` is pure functions, no I/O. `llm` and `rag` are used by workflows only. Routers never run SQL or call the LLM.

## Hard rules (CI and reviewers check these)
- PostgreSQL is the only database. No SQLite, Redis, or vector store. RAG is Postgres full-text search.
- Plain parameterised SQL through psycopg 3 (`%s`, `%(name)s`, `psycopg.sql.Identifier`). Never f-string SQL. No ORM.
- Human approval is a real LangGraph `interrupt()` with `Command(resume=...)`, checkpointed by `PostgresSaver`. Never a status flag. `InMemorySaver` only in `experiments/`.
- `is_emergency()` is deterministic code and runs before any LLM call. The LLM can never decide something is not an emergency.
- Never diagnose or recommend treatment (prompt rule plus output guard).
- Clinic answers come only from clinic documents and name the source; otherwise say it is not covered.
- Every LLM call goes through `LLMPort`. All tests and CI use `FakeLLM`; CI has no OpenAI key.
- Time-dependent SQL takes `%(now)s` from the injected `Clock`, never SQL `now()`.
- Double booking must be impossible in SQL (conditional UPDATE + partial unique index).
- Config from env vars, fail fast with a clear message. No secrets in Git. Every external call has a timeout. LLM/DB failures return JSON 503 and log.
- Fully synchronous: `def` endpoints, sync pool, sync OpenAI client. One uvicorn worker.
- Typed code (mypy strict). No `type: ignore` without a comment. Coverage at least 80%.

## Commands
```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov=clinic --cov-fail-under=80
docker compose up --build -d --wait
```

## Git
- One issue per branch: `feature/CFD-xx-<name>-<title>`. PR title `CFD-xx: <title>`. Squash merge. No direct pushes to `main`.
- Tests in the same PR as the code. Update `docs/` if a contract changes.

## Working with Claude Code
- Do not read or print `.env`. Do not commit secrets.
- Keep changes inside the issue's scope. Do not start another issue's work.
- Humans own every line: be ready to explain the PR.
