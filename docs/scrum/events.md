# Events

## Standup notes

**Not recorded.** No standup notes, dates or attendance were written down during the work. Nothing is reconstructed here.

What exists instead is the trace in GitHub: issue and PR comments, and review threads. Slack direct messages between the team lead and teammates also exist, but they are not in the repository or on GitHub and were not reviewed for this note.

## Mid-day sync: what was cut and why

**No sync note was kept.** Scope changes that the repository does prove:

| What | Evidence | Reason on record |
| --- | --- | --- |
| Work after v0.1.0 went to a separate branch, `main` frozen at tag `v0.1.0` | Branch `v0.1.1`, ruleset `protect-main` | See decisions.md |
| Should/could stories beyond CFD-29, CFD-30 and CFD-34 were not created | Issue list stops at #50 (plus bug #61 and #56) | See decisions.md |
| PR #35 (CFD-14 approval wiring) closed unmerged | PR #35 state CLOSED; #34 and #25 merged for the same story | Reason not recorded in a note |
| PRs #18 and #20 (CI debugging output) closed unmerged | Both CLOSED | Reason not recorded |

Anything else that was cut or reduced and not listed here was not recorded.

## Review notes

Review of the finished work, from GitHub:

- 32 PRs were merged, 3 were closed unmerged (#18, #20, #35).
- Merged PRs were squash merged under the `protect-main` ruleset, which requires the `quality` CI check. Whether every PR got a `claude-review` comment was not checked.
- Merged PRs by author: Ahmed 14, Mohammed 10, Shaheen 8.
- Release v0.1.0 was cut from `main` (PR #38); the smoke test against the real model needed a follow-up (#39).
- v0.1.1 added the SQL schema and seed, the architecture pack, the three research notes with ADRs, the audit list, the rollback rehearsal and the fresh-clone test.
- The sprint review demo itself, if one was held, was not recorded.
