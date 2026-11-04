# Fresh-clone test (CFD-35)

Done by MrSn001 (did not write the compose file) on 2026-11-04, before the v0.1.1 release.

## What was tested

- **Code:** branch `v0.1.1` at `25e305f` ("CFD-34: Seed: 6 clinic documents and emergency
  sample messages"). There was no `v0.1.1` tag yet, so the tip of the release branch was tested.
  Re-run with the tag once it exists.
- **Machine:** Windows 11 Pro PC, WSL2 distro Ubuntu 26.04.1 LTS (kernel
  6.18.54.1-microsoft-standard-WSL2), with its own Docker Engine 29.9.2 and Docker Compose v5.7.0,
  git 2.53.0, curl and jq. Ubuntu's own Python 3.14 and uv are also installed there but were not
  used: the app, its database and its dependencies ran only in the containers. It is a separate
  Linux environment on a team member's PC, not a second PC.
- **git setting:** this distro has `core.autocrlf=true` in its global git config. That turned out
  to matter (surprise 1).
- **Model:** `LLM_PROVIDER=fake`, so no API key was used.

## Commands

Before the test, an older v0.1.0 demo stack in the same Docker Engine was removed with
`docker compose down -v` (see surprise 2). Then, as in the README:

```bash
git clone https://github.com/lqji/anthropic.git
cd anthropic
git checkout 25e305f
cp .env.example .env
# in .env: LLM_PROVIDER=fake (the only change)
docker compose up --build -d --wait
curl localhost:8000/health
scripts/smoke_test.sh
```

## Results

| Run | What | Result |
|---|---|---|
| 1 | Fresh clone, `up --build -d --wait` | Both services healthy in 28 s, `/health` ok. The smoke test did not start: `env: 'bash\r': No such file or directory` (surprise 1). After re-checking out the files with LF endings: **8/8 checks passed** (emergency check skipped by default). |
| 2 | `docker compose down -v`, then `up --build -d --wait` | Both healthy in 17 s. `RUN_EMERGENCY=1 scripts/smoke_test.sh`: **9/9 checks passed**. |
| 3 | Book an appointment, `docker compose restart db` | **Data survived.** Before and after: 5 conversations, 36 messages, 2 confirmed bookings, 6 documents. The conversation read back with its booking. But the first requests after the restart got **HTTP 503** (surprise 3); it recovered by itself and the next smoke test passed 8/8. |
| 4 | Fresh clone of this PR's branch (with the `.gitattributes` fix), still with `core.autocrlf=true`, `down -v` first | 0 files with CRLF. Both healthy in 11 s. `RUN_EMERGENCY=1 scripts/smoke_test.sh`: **9/9 checks passed**, no workaround. |

Build times are with Docker's build cache from earlier builds on this engine, so they are shorter
than a first build on a new machine.

## Surprises

1. **CRLF line endings broke the smoke test (fixed in this PR).** With `core.autocrlf=true`, git
   checked out every file with Windows line endings, also on Linux. The containers still started,
   but `scripts/smoke_test.sh` failed at once with `env: 'bash\r': No such file or directory`. The
   repository had no `.gitattributes`. **Fix:** `.gitattributes` with `* text=auto eol=lf`, so
   every text file is checked out with LF whatever the machine's git says. The files are already
   stored with LF, so nothing else changes. Checked in run 4.
2. **One copy per Docker Engine (README line added in this PR).** `compose.yaml` fixes the project
   name `clinic-front-desk`. A second checkout on the same Docker Engine (here, an old v0.1.0
   checkout) uses the same containers and the same database volume, so a "fresh" start would
   have reused the old database. The README did not say this. **Fix:** a README line under "Run
   it": stop the other checkout first.
3. **HTTP 503 for the first requests after a database restart (not fixed here).** After
   `docker compose restart db`, 2 requests got `503 database_unavailable` before the 3rd
   succeeded. The next smoke test then failed once in the chat step, because the workflow's
   checkpoint pool still held its own closed connection. The api log shows `psycopg.pool:
   discarding closed connection` and `AdminShutdown`. The pools in `db.make_pool` and
   `db.make_checkpoint_pool` hand out each dead connection once and drop it only after a request
   fails on it. Nothing is lost and it clears by itself, but a patient's message can fail right
   after a database restart. This is a code change, outside a fresh-clone test, so it is left to
   a follow-up issue (for example, checking connections before they are handed out).
4. **`/health` reports version `0.1.0`** on the `v0.1.1` branch. Expected until the release
   bumps `src/clinic/__init__.py`; worth checking after the release.

No variable was missing and no manual step was needed beyond the README: `cp .env.example .env`
and one line for the model provider were enough.

## Secrets

`.env` was created from `.env.example` and only `LLM_PROVIDER` was changed with `sed`; it was
never printed. No API key was used. Nothing from `.env` is in this record or the PR.
