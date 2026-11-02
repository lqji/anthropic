# Clinic Front-Desk Assistant

REST API that acts as a clinic's front desk: answers questions from the clinic's own documents, finds and books appointment slots, and pauses for staff approval on cancellations and same-day bookings. Emergencies are caught by deterministic code before any model call; the assistant never diagnoses.

**Stack:** Python 3.13, FastAPI, LangGraph, PostgreSQL 16 (only database), psycopg 3 with plain parameterised SQL (no ORM), OpenAI behind an `LLMPort` (tests use a deterministic `FakeLLM`), Docker Compose.

## Run it

```bash
cp .env.example .env        # add OPENAI_API_KEY; everything else has working defaults
docker compose up --build -d --wait
curl localhost:8000/health
```

Interactive API docs: <http://localhost:8000/docs>. No API key? Set `LLM_PROVIDER=fake` for a deterministic demo mode.

## Develop and test

```bash
uv sync --locked
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov=clinic --cov-fail-under=80   # needs Postgres, see DATABASE_URL in .env.example
```

CI runs the same commands against a Postgres service with no OpenAI key.

## Team workflow

| Who | GitHub | Role |
|---|---|---|
| A | @lqji | Lead / PO |
| B | @Mohammed98112 | Developer |
| C | @MrSn001 | Developer |

1. Pick your issue from the board. One issue per branch.
2. Branch: `feature/CFD-xx-<your-name>-<short-title>`.
3. PR title: `CFD-xx: <title>`, body `Closes #<issue>`. Tests go in the same PR as the code.
4. CI (`quality`) must pass. Claude posts an automatic review on every non-draft PR: answer every point (reply or fix-up commit).
5. Two teammates approve, each leaving at least one real comment. Squash merge only.
6. Everyone must be able to explain every PR, including ones written with Claude Code.

Rules that reviewers check are in [AGENTS.md](AGENTS.md).

## Configuration

All settings are environment variables; the service refuses to start and names each missing one. See `.env.example` for the full list. Never commit `.env`.

## Deploy and roll back

Filled in by CFD-16 (VM runbook, `scripts/smoke_test.sh`, rollback between two locally built image tags).

## Known limitations

- No authentication on `/api/v1` (trusted network only; put an authenticating proxy in front).
- A pending approval never expires.
- While a conversation waits for approval, every non-emergency message gets "still waiting".
- English only.
