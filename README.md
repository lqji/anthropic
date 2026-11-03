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

## Team workflow and agreements

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

Agreements:
- Nobody pushes to `main`; everything goes through a PR and a squash merge, one commit per PR.
- Secrets stay out of Git: `.env` is ignored, and nobody pastes keys into issues, PRs or chats.
- A contract change (API, schema, env var) updates `docs/` and `.env.example` in the same PR.
- A failing smoke test after a deploy means roll back first, then investigate.

## Configuration

All settings are environment variables; the service refuses to start and names each missing one. Compose reads them from `.env` (copy `.env.example`). Never commit `.env`.

| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `clinic`, `change-me-local-only`, `clinic` | The database container. **Change the password on any shared machine.** Compose builds `DATABASE_URL` for the api from these. |
| `DATABASE_URL` | none | Required when running outside compose (tests, `uv run`). |
| `DB_POOL_MAX_SIZE`, `DB_POOL_TIMEOUT_S` | `20`, `5` | Connection pool size and wait before a request gets 503. |
| `LLM_PROVIDER` | `openai` | `openai`, or `fake` for a deterministic demo mode that needs no key. |
| `OPENAI_API_KEY` | none | Required when `LLM_PROVIDER=openai`. |
| `OPENAI_MODEL`, `LLM_TIMEOUT_S`, `LLM_MAX_RETRIES` | `gpt-4o-mini`, `20`, `1` | Model and its time limit. A failing model gives a 503 `llm_unavailable`. |
| `CLINIC_TIMEZONE` | `Asia/Muscat` | The clinic's calendar: "same day" and slot times follow it. |
| `EMERGENCY_NUMBER` | `9999` | Told to a patient whose message matches an emergency rule. |
| `HISTORY_WINDOW` | `10` | Earlier messages given to the model. |
| `INTENT_MIN_CONFIDENCE` | `0.6` | Below this the assistant asks what the patient needs. |
| `RAG_MIN_SCORE`, `RAG_TOP_K` | `0.30`, `3` | Minimum score and number of document sections used to answer. |
| `CLINIC_DOCS_DIR` | `seed/clinic_docs` | Markdown documents loaded at startup. |
| `LANGGRAPH_STRICT_MSGPACK` | `true` | Keep `true`: checkpoints may only hold plain data. |
| `APP_VERSION` | `dev` | Tags the image (`clinic-api:<version>`) and is shown by `/health`. |

## Deploy and roll back

The app runs from two containers (api and PostgreSQL) on one Ubuntu VM. Images are built on the VM
from the Git tag being deployed: there is no registry. The image is named
`clinic-api:${APP_VERSION}` and compose always rebuilds it (`pull_policy: build`), so the tag you
deploy is exactly the code you checked out.

### Provision the VM (once)

On a fresh Ubuntu LTS VM, as a user with `sudo`:

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git jq
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"    # then log out and back in
```

Then clone and configure:

```bash
git clone https://github.com/lqji/anthropic.git clinic && cd clinic
cp .env.example .env
# edit .env: set OPENAI_API_KEY (or LLM_PROVIDER=fake) and change POSTGRES_PASSWORD
```

There is no authentication on `/api/v1`: keep port 8000 on a trusted network, or put an
authenticating proxy in front of it.

### Deploy a version

```bash
git fetch --tags && git checkout <tag>          # for example v0.1.0
APP_VERSION=<tag> docker compose up --build -d --wait
scripts/smoke_test.sh                           # http://localhost:8000, or pass another base URL
```

`--wait` returns only when both services are healthy. The smoke test then walks through every
feature: health, a clinic question, a question the documents do not cover, a booking, a
cancellation that waits for staff, and an emergency. It prints one `ok` line per check, stops at
the first failure with the check name and the response, and exits 1 (it exits 0 only if every check
passed). Run it after every deploy.

The database lives in the `pgdata` volume, so a normal deploy keeps all data. **First deploy or a
clean demo:** `docker compose down -v` deletes the volume, and the demo slots are seeded again for
the next 5 days. Never run `down -v` on data you want to keep.

### Roll back

A rollback is a deploy of the previous tag: the code and image change, the database stays.

```bash
git checkout <previous-tag>
APP_VERSION=<previous-tag> docker compose up --build -d --wait
scripts/smoke_test.sh
```

Both images stay on the VM (`docker image ls clinic-api`), so building the older tag again is fast
and nothing comes from a registry. Startup only adds what is missing and drops nothing, but a
newer release may add a column or constraint that the older code does not expect. Roll back like
this only to a release with the same database schema, and read the release notes first. Before a
release that changes the database, take a backup. The dump holds patient data: it is written
readable by you only, `backup-*.sql` is ignored by Git, and you delete it when it is no longer
needed.

```bash
(umask 077; docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > backup-$(date +%F).sql)
```

To go back to an older release whose code does not work with the newer schema, restore the backup
taken before the upgrade (**this discards everything saved since**):

```bash
docker compose down -v                      # deletes the database volume
docker compose up -d --wait db              # a new, empty database
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' < backup-<date>.sql
git checkout <previous-tag>
APP_VERSION=<previous-tag> docker compose up --build -d --wait
scripts/smoke_test.sh
```

### If something is wrong

| Symptom | Look at |
|---|---|
| `up --wait` times out | `docker compose ps`, then `docker compose logs api db` |
| smoke test fails at `health` with 503 | the database is down or unreachable: `docker compose ps db` |
| smoke test fails at `free slots` | the demo slots ran out (5 days): `docker compose down -v` and deploy again |
| a request gets 503 `llm_unavailable` | `OPENAI_API_KEY`, or the provider is down; `LLM_PROVIDER=fake` keeps the demo running |

## Known limitations

- No authentication on `/api/v1` (trusted network only; put an authenticating proxy in front).
- A pending approval never expires.
- While a conversation waits for approval, every non-emergency message gets "still waiting".
- English only.
- Demo slots are seeded once, on the first start, for 5 days from that day (2 doctors, 30-minute
  slots 09:00-13:00 and 14:00-19:00). They do not roll forward: after 5 days there are no future
  slots until the database is reset with `docker compose down -v`.
