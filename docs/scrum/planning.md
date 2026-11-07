# Planning

## Sprint goal

Not recorded as a sentence at the time. The product goal is the one in the README: a REST API that acts as a clinic's front desk, answers from the clinic's own documents, finds and books slots, pauses for staff approval on cancellations and same-day bookings, catches emergencies in code before any model call, and never diagnoses.

The work ran as two releases:

- **v0.1.0** (CFD-01 to CFD-16, CFD-24): the working service, merged to `main` on 2026-11-02 and 2026-11-03.
- **v0.1.1** (CFD-25 to CFD-35): documentation, research notes, seed data and rehearsals, merged to branch `v0.1.1` on 2026-11-04 and 2026-11-05.

## Stories

Acceptance criteria are in each issue (the issue number is the link). They are not copied here, so there is one copy only. Points are the ones written in the issue body.

| Story | Issue | Owner | Points | Label | State |
| --- | --- | --- | --- | --- | --- |
| CFD-01 Repo bootstrap | #1 | A | 0 | must | closed |
| CFD-03 Service skeleton | #2 | A | 3 | must | closed |
| CFD-04 LLM port | #3 | A | 3 | must | closed |
| CFD-05 Checkpointed workflow | #4 | C | 3 | must | closed |
| CFD-06 Conversation REST API | #5 | B | 2 | must | closed |
| CFD-07 Intent classification | #6 | A | 3 | must | closed |
| CFD-08 Emergency short-circuit | #7 | B | 3 | must | closed |
| CFD-09 Never diagnose | #8 | A | 2 | must | closed |
| CFD-10 Clinic documents | #9 | B | 3 | must | closed |
| CFD-11 Grounded answers | #10 | C | 3 | must | closed |
| CFD-12 Doctors and slots | #11 | C | 3 | must | closed |
| CFD-13 Atomic booking | #12 | C | 5 | must | closed |
| CFD-14 Cancellation approval | #13 | B | 5 | must | closed |
| CFD-15 Same-day approval | #14 | B | 2 | must | closed |
| CFD-16 Deployment runbook | #15 | A | 3 | must | closed |
| CFD-24 Release v0.1.0 | #16 | A | 0 | must | closed |
| CFD-25 Schema and seed as SQL | #40 | A | 5 | must | closed |
| CFD-26 Architecture pack | #41 | A | 5 | must | closed |
| CFD-27 Scrum records | #42 | A | 2 | must | open until this PR merges |
| CFD-28 Research R2 (human in the loop) | #43 | B | 3 | must | closed |
| CFD-29 Seed 4 doctors | #44 | B | 2 | should | closed |
| CFD-30 Audit list | #45 | B | 5 | should | closed |
| CFD-31 Rollback rehearsal | #46 | B | 2 | must | closed |
| CFD-32 Research R1 (checkpoints) | #47 | C | 3 | must | closed |
| CFD-33 Research R3 (RAG) | #48 | C | 3 | must | closed |
| CFD-34 Seed documents and samples | #49 | C | 2 | should | closed |
| CFD-35 Fresh-clone test | #50 | C | 2 | must | closed |

Two unplanned items appeared during the work and are not in the table: issue #61 (HTTP 503 after a database restart, fixed by PR #63 "CFD-61") and issue #56 (a docstring that contradicts the code in `make_checkpoint_pool`, still open).

## Definition of Ready and Done

No separate Definition of Ready or Definition of Done was written down before the work began. What the team actually enforced is written in `AGENTS.md`, `.github/pull_request_template.md` and the `protect-main` ruleset, and is recorded here as practised:

**Ready (as practised):** the issue has an owner, points, a label, a `Depends:` line and an acceptance-criteria checklist.

**Done (as practised):**

- One issue per branch, named `feature/CFD-xx-<name>-<title>`; PR titled `CFD-xx: <title>` and starting with `Closes #<n>`.
- CI check `quality` passes (ruff, mypy, pytest with coverage of at least 80%).
- Tests are in the same PR as the code; `docs/` is updated if a contract changes.
- The Claude review comment has a reply or a fix for every point.
- At least one approving review (the ruleset requires one, code-owner review and last-push approval); squash merge only.
- The issue is closed. On `v0.1.1` this is done by hand, because `Closes #n` does not auto-close on a non-default branch.

## Planning risks

Not recorded as a list during planning. Risks that the repository does record elsewhere:

- `docs/architecture.md` holds the architecture risks (CFD-26).
- `docs/research/R1`, `R2`, `R3` and the ADRs in `docs/adr/` hold the technical risks behind checkpoints, approval and RAG.

Risks that actually happened, visible in the history: stacked PRs that had to be rebased (#22, #24, #23, #25 for CFD-10, 06, 08, 14); two PRs for the same story (#34 and #35 for CFD-14); a release smoke test that failed against the real model (fixed in #39); a dead-connection bug after a database restart (#61).
