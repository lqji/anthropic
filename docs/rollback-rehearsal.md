# Rollback rehearsal (CFD-31)

The brief requires "rollback demonstrated". The runbook is in the README ("Deploy a version",
"Roll back"). This page records one pass through it by a person who did not write it.

## Setup

| | |
|---|---|
| Date | 2026-11-05 (about 03:55-04:00 UTC) |
| Done by | Mohammed (Owner B). He did not write the runbook (CFD-16). |
| Host | **Ubuntu 26.04.1 LTS on WSL2** (kernel 6.18), Docker 29.9.1, Compose v5.6.0. This is **not** the team's Ubuntu Server VM: see "What this does not show". |
| Tags | forward `v0.1.0` (commit 72cc4c8), previous `v0.1.0-rc.1` (commit f0916be) |
| Model | `LLM_PROVIDER=fake`, so the smoke test needs no OpenAI key and is exact |
| Checkout | fresh `git clone https://github.com/lqji/anthropic.git`, then `cp .env.example .env` |

## Commands and results

Every command is the README one. `COMPOSE_PROJECT_NAME=rehearsal` was set so that the rehearsal
had its own containers and volume (see "Findings").

### 1. Deploy v0.1.0

```bash
git fetch --tags && git checkout v0.1.0
APP_VERSION=0.1.0 docker compose up --build -d --wait
curl -s http://127.0.0.1:8000/health
bash scripts/smoke_test.sh
```

```text
{"status":"ok","database":"ok","version":"0.1.0"}
ok   health: service and database are up
ok   doctors: Dr. Khalid Rahman and others are listed
ok   free slots: Dr. Khalid Rahman has slots on 2026-11-06
ok   conversation: started for smoke-test-1793851145-14972
ok   clinic question: answered from visit-preparation.md
ok   not in documents: the front desk is suggested instead of a guess
ok   booking: confirmed at once with Dr. Khalid Rahman for 2026-11-06
ok   cancellation: paused for staff, approved, booking cancelled
skip emergency check (set RUN_EMERGENCY=1 to include it; it leaves a flag for staff)
Smoke test passed: 8 checks against http://localhost:8000 (the emergency check was skipped)
```

Exit code 0.

### 2. Roll back to v0.1.0-rc.1

```bash
SMOKE="$(mktemp)" && cp scripts/smoke_test.sh "$SMOKE"
git checkout v0.1.0-rc.1
APP_VERSION=0.1.0-rc.1 docker compose up --build -d --wait
curl -s http://127.0.0.1:8000/health
bash "$SMOKE"; rm -f "$SMOKE"
```

```text
{"status":"ok","database":"ok","version":"0.1.0-rc.1"}
ok   health: service and database are up
ok   doctors: Dr. Khalid Rahman and others are listed
ok   free slots: Dr. Khalid Rahman has slots on 2026-11-06
ok   conversation: started for smoke-test-1793851177-15194
ok   clinic question: answered from visit-preparation.md
ok   not in documents: the front desk is suggested instead of a guess
ok   booking: confirmed at once with Dr. Khalid Rahman for 2026-11-06
ok   cancellation: paused for staff, approved, booking cancelled
skip emergency check (set RUN_EMERGENCY=1 to include it; it leaves a flag for staff)
Smoke test passed: 8 checks against http://localhost:8000 (the emergency check was skipped)
```

Exit code 0. `/health` reports `0.1.0-rc.1`, so the old code is really running. Both images were
on the host afterwards (`clinic-api:0.1.0` and `clinic-api:0.1.0-rc.1`).

### 3. Go forward to v0.1.0 again

```bash
git checkout v0.1.0
APP_VERSION=0.1.0 docker compose up --build -d --wait
curl -s http://127.0.0.1:8000/health
bash scripts/smoke_test.sh
```

```text
{"status":"ok","database":"ok","version":"0.1.0"}
ok   health: service and database are up
ok   doctors: Dr. Khalid Rahman and others are listed
ok   free slots: Dr. Khalid Rahman has slots on 2026-11-06
ok   conversation: started for smoke-test-1793851185-15413
ok   clinic question: answered from visit-preparation.md
ok   not in documents: the front desk is suggested instead of a guess
ok   booking: confirmed at once with Dr. Khalid Rahman for 2026-11-06
ok   cancellation: paused for staff, approved, booking cancelled
skip emergency check (set RUN_EMERGENCY=1 to include it; it leaves a flag for staff)
Smoke test passed: 8 checks against http://localhost:8000 (the emergency check was skipped)
```

Exit code 0.

### Second pass, README commands only

The output above is from the first pass, which used `COMPOSE_PROJECT_NAME=rehearsal` to keep
away from a development stack on the same host. I repeated all three steps (same clone, 2026-11-05
about 04:07 UTC) with port 8000 free, the default project name and no extra variables, exactly as
the README now reads. `/health` reported `0.1.0`, then `0.1.0-rc.1` after the rollback, then
`0.1.0` again; each smoke test passed 8 of 8 (exit 0).

One mistake between the two passes, kept here because it shows why finding 1 matters: a repeat
run on this host while the first pass's containers still held port 8000 failed to start the new
API container (`port is already allocated`). The smoke tests still passed, against the old
container, and `/health` showed the wrong version after the "rollback". Always check `/health`.

### Data check across the rollback

Added after review. On `v0.1.0` I started a conversation (`patient_ref` `rehearsal-keep`), asked
"How should I prepare for my visit?" and saved `GET /api/v1/conversations/{id}` (863 bytes of
JSON, keys sorted). I rolled back to `v0.1.0-rc.1` (`/health` said `0.1.0-rc.1`) and fetched it
again, then went forward (`/health` said `0.1.0`) and fetched it a third time. `cmp` found all
three identical: the conversation and its messages survive a rollback and a roll-forward.

## Findings

The runbook worked as written. Three things were unclear or missing, all fixed in the README in
this PR:

1. **A second copy of the stack on the same host blocks the deploy.** `compose.yaml` fixes the
   project name (`clinic-front-desk`) and port 8000. The host already ran a development copy, so
   it had to be stopped first. The README now says so in "Deploy a version".
2. **The rollback block did not check the version.** The deploy section says `/health` must match
   the tag, but the rollback commands did not include that check, and it is the only proof that
   the old code is running. A `curl` of `/health` is added to the rollback block.
3. **"Go forward" had no commands.** The README said "run `git checkout <tag>` and deploy it as
   above". The forward commands are now written out, with the health check and smoke test.

Also noted, not changed: the `.env.example` line `DATABASE_URL=...@localhost:5432/...` is for
running outside Compose; Compose sets the container's own value, so it is harmless on the VM. The
emergency check was skipped, as the README says it is by default.

## What this does not show

- **Not the team's Ubuntu Server VM.** The issue asks for that VM; this pass ran on WSL2 Ubuntu
  because that is the Linux host the person doing it had at hand. The commands are plain Docker and
  Git, but the VM pass is still to do: run the same three steps there and add its output below.
- **Same database schema on both tags.** The two tags share one schema, so this does not test the
  README's backup-and-restore path for a release with a different schema.
- **Data survival was checked with one conversation only** (see the data check above). Bookings
  and approvals were not compared.
- **Build time and downloads were not measured.** The host had built this project before, so its
  cache was probably warm; a fresh host downloads base images and packages for each tag.
- **Fake model.** With a real OpenAI model, the "booking" and "not in documents" checks can need
  a manual look (see the README).
