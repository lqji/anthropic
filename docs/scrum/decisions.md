# Product Owner decisions

Each decision has a one-line reason. Where the reason was not written down at the time, it says so.

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-11-02 | One issue per branch, PR title `CFD-xx: <title>`, squash merge only, no direct push to `main` | Keeps history clean and every change tied to one issue (`AGENTS.md`). |
| 2026-11-02 | PostgreSQL is the only database; human approval is a real `interrupt()`; emergencies are caught by code before the LLM | These are hard rules in `AGENTS.md`; the date and the reason for choosing them were not recorded. |
| 2026-11-03 | Release v0.1.0 from `main` once all Must stories were merged | Reason not recorded. |
| 2026-11-03 | Close PR #35 and keep #34 and #25 for CFD-14 | Two PRs covered the same story; one had to go. Further reason not recorded. |
| 2026-11-04 | Freeze `main` at `v0.1.0`; all later work goes to branch `v0.1.1`, merged to `main` in one release PR | Nothing in `main` should change until all should/could work is approved and working. |
| 2026-11-04 | Keep `main` as the default branch | Confirmed by the team lead; reason not recorded beyond the confirmation. |
| 2026-11-04 | Do not create further should/could issues after CFD-35 | Team lead's instruction; reason not recorded. |
| 2026-11-04 | Add `v0.1.1` to the `protect-main` ruleset by name | Reason not recorded. |
| 2026-11-04 | Open issue #61 as a bug and fix it in its own PR (#63) instead of folding it into another story | It was a real defect found after a database restart, outside any story's scope. |
