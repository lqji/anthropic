# Retrospective

Written 2026-11-05 by the Scrum Master from the repository. It is a draft until the team has read it and agreed the actions; it is not a record of a meeting held.

## What worked

- Every story had an owner, points and an acceptance-criteria checklist, and every story was closed.
- CI, the Claude review and the ruleset caught problems before merge (for example the real-model smoke test fix in #39).
- Splitting v0.1.1 off `main` kept the released service stable while documentation work continued.

## What did not work

- No standup notes, mid-day sync note, sprint goal, Definition of Ready/Done or planning-risk list was kept at the time (see events.md and planning.md).
- Stacked PRs and two PRs for one story (#34, #35) cost rebasing and review time.
- The release smoke test failed against the real model after v0.1.0 was cut, and a dead-connection bug (#61) appeared after a database restart. Neither was planned for.
- Issue #56 (docstring says "no prepared statements", code prepares every statement) is still open.

## Process gap 1: authorship balance

Measured from git on branch `v0.1.1` on 2026-11-05:

| Member | Merged PRs (of 32) | Lines added | Lines added, share |
| --- | --- | --- | --- |
| Ahmed (A) | 14 (44%) | 5,458 | 29.5% |
| Mohammed (B) | 10 (31%) | 7,534 | 40.8% |
| Shaheen (C) | 8 (25%) | 5,485 | 29.7% |

The team lead authored the most PRs; Mohammed added the most lines (seed data, SQL and documentation count as lines). Shaheen authored the fewest PRs. Lines are a crude measure: they include documentation and seed files.

Also true and relevant to authorship: 12 commits on `v0.1.1` carry a `Co-Authored-By: Claude` trailer. The repository rule is that humans own every line (`AGENTS.md`).

## Process gap 2: points per member against the 30% rule

The text of the 30% rule is not in this repository, the planning kit or the issues, so it is applied here as "each member should carry at least 30% of the points". If the rule means something else, this section needs correcting.

| Member | v0.1.0 points | v0.1.1 points | Total | Share of total |
| --- | --- | --- | --- | --- |
| Ahmed (A) | 14 | 12 | 26 | 33.8% |
| Mohammed (B) | 15 | 12 | 27 | 35.1% |
| Shaheen (C) | 14 | 10 | 24 | 31.2% |

Over both releases everyone is above 30%. In v0.1.1 alone Shaheen carries 10 of 34 points, 29.4%, which is below 30%. Two stories on the Ahmed side, CFD-01 and CFD-24, are worth 0 points although they took real work, so the points understate the team lead's effort and the table is not a measure of hours.

## Actions

Owners and dates are proposed by the Scrum Master and are for the team to confirm or change.

| # | Action | Owner | Date |
| --- | --- | --- | --- |
| 1 | Write a sprint goal and a Definition of Ready and Done into `docs/scrum/planning.md` before the next sprint starts | Ahmed | 2026-11-09 |
| 2 | Keep dated standup and mid-day-sync notes in `docs/scrum/events.md` from the next working day | Ahmed | 2026-11-06 |
| 3 | Assign the next stories so each member carries at least 30% of the points of the sprint, and write the rule's text next to the points table | Ahmed | 2026-11-09 |
| 4 | Fix issue #56: choose `prepare_threshold` 0 or `None`, make docstring and code agree, add a test and update the ADR | Shaheen (issue author, to confirm) | 2026-11-12 |
| 5 | Reply to this draft with corrections or objections, then mark it agreed | Mohammed and Shaheen | 2026-11-09 |
