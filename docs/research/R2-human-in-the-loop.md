# R2: Human in the loop

Issue: CFD-28. Owner: Mohammed. Experiment: `experiments/hitl/`. Decision: `docs/adr/approval-design.md`.

## Question

How does a workflow pause for staff approval and later resume with the staff decision through the
API? What does the patient see while it is paused?

## What we need

A patient asks to cancel an appointment (or to book one on the same day). A person at the front
desk must say yes or no before anything changes. Between the question and the answer can be
minutes or days, and the service may restart. So the wait cannot live in a Python variable or a
thread: it has to be stored, and the decision has to find its way back to the exact place where
the workflow stopped.

## Options compared

| | A. `interrupt()` + `Command(resume=...)` | B. Status flag in our tables | C. `interrupt_before=[...]` |
|---|---|---|---|
| Where the wait is stored | In the LangGraph checkpoint, with the whole state | In a column we read and write ourselves | In the checkpoint, before the named node |
| Resume | One call: `invoke(Command(resume=value))` | We write the code that finds the place and restarts the flow | `invoke(None, config)`, optionally after editing the state |
| What the staff answer looks like inside the graph | The return value of `interrupt()`, in the node that asked | We must carry it into the state ourselves | Not a return value: written into the state with `update_state` |
| Payload to the person who decides | `interrupt(payload)` | Our own columns | None; the caller reads the state |
| Survives a restart | Yes with `PostgresSaver` | Yes, but the position in the flow does not | Yes with `PostgresSaver` |
| Fit with `AGENTS.md` | Required | Forbidden ("never a status flag") | Not the chosen design |

Only option A was run (see below). B and C are compared from the documentation and from reading
how our code is built; I did not build them.

Why not B: a flag says "waiting", but not where in the workflow to continue or with which
variables. We would rebuild half of what a checkpoint already stores, and every bug in that
rebuilt half is a bug in how a patient is treated. Why not C: it stops before a node runs, which
is fine for "review before an action". We want the question itself to live in the node that asks
it, and the answer to come back as the value of that call, so the code reads in the order it
happens.

## Sources

- LangGraph, interrupts: <https://docs.langchain.com/oss/python/langgraph/interrupts>
- LangGraph, persistence and checkpointers: <https://docs.langchain.com/oss/python/langgraph/persistence>
- `langgraph-checkpoint-postgres` (the `PostgresSaver` we use): <https://pypi.org/project/langgraph-checkpoint-postgres/>

## What the experiment showed

`experiments/hitl/hitl_demo.py` is a four-node graph with `FakeLLM` and `InMemorySaver`. Run it
with `uv run python experiments/hitl/hitl_demo.py`. It showed five things:

1. **The call returns instead of waiting.** `invoke` came back as soon as it reached
   `interrupt()`, with the payload under `__interrupt__`. A web request can therefore answer the
   patient immediately; nothing blocks while staff think.
2. **The pause belongs to one conversation.** The state is stored under the `thread_id`. A second
   thread ran to the end while the first one was paused.
3. **The waiting state is in the saver, not in the graph object.** A new graph built on the same
   saver reported the same `next` node. With `PostgresSaver` the saver is a database, so this is
   what lets the pause survive a restart. (`InMemorySaver` does not survive one; the service tests
   check the restart with Postgres.)
4. **Resume re-runs the waiting node from its first line.** `ask_staff` started twice for one
   conversation: once to pause, once to continue. `interrupt()` then returned the staff answer.
   Anything written before `interrupt()` happens twice.
5. **Both answers use the same code path.** Approve and reject are two values of the same resume
   call; the next node reads the value and picks the branch.

Finding 4 is the one that shapes the design. It is why `await_approval` in
`workflows/approval.py` does nothing but call `interrupt()`, and why the approval row is created
in a separate node (`request_approval`) that runs before the pause.

## What the service does today

- `cancel` finds the one booking; `request_approval` stores a pending approval and marks the
  conversation `awaiting_approval`; `await_approval` calls `interrupt()`; `apply_decision` carries
  out the decision (cancellation and same-day booking share it).
- Staff call `POST /api/v1/approvals/{id}/approve` or `/reject`. The decision is saved with a
  conditional UPDATE (only one decision can win), then `GraphApprovalResumer` calls
  `Command(resume=...)` on the same thread, and the patient's message is stored.
- If the process dies after the decision is saved and before the resume finishes, repeating the
  same call completes it, and so does the reconcile that runs at startup.

## What the patient sees while paused

The request itself gets a reply such as "I've asked our front-desk staff to approve cancelling
your appointment ...", with `status: "awaiting_approval"` and an `approval_id`. While the
approval is pending, any new routine message gets the fixed reply "We're still waiting for our
front-desk staff to decide on your request. I'll message you here as soon as they do." The graph
is not run for those messages. An emergency is still checked first and answered, because
`is_emergency()` runs before anything else. When staff decide, the result is stored as the next
assistant message in the same conversation; the patient reads it the next time they open the chat
(the API has no push channel).

A note on the rule "never a status flag": `conversations.status` does hold `awaiting_approval`,
but only to answer "still waiting" without loading the graph. It is a copy for reading. The
pause itself is the checkpoint; if the two ever disagreed, the checkpoint decides what happens on
resume.

## Recommendation

Use `interrupt()` and `Command(resume=...)` with `PostgresSaver`, as the code does. Keep
everything before `interrupt()` free of side effects, and put each side effect in a node that is
safe to run twice. This is what ADR `approval-design` records.

## Risks and known limitations

- **A pending approval never expires.** There is no timeout, reminder or escalation. A patient
  whose request nobody opens stays in "still waiting" until someone decides. This is a known
  limitation of this version, not a design goal.
- **No staff login.** Anyone who can reach the API can decide; `decided_by` is free text.
- **One process.** The conversation locks live in memory, so the service runs one worker (as the
  project rules require). Two workers would need a shared lock.
- **Re-run on resume.** A node that is edited later to do something before `interrupt()` would
  repeat it. The tests that crash between steps guard the current nodes, not future ones.
- **Checkpoint format.** Checkpointed state must be simple data. That is why the approval state
  holds strings and numbers, not objects.
- **The patient has to ask.** There is no notification; the decision appears at the patient's next
  visit to the chat.

## Agreement with R1 (CFD-32)

Compared with the R1 note (CFD-32, issue #47, MrSn001), which is in **PR #53 and not merged yet**:
`docs/research/R1-checkpoints.md` and `docs/adr/checkpoint-design.md`. Those two files are not on
this branch, so the links below work once #53 is merged. MrSn001 made the comparison in a comment
on PR #52 and I checked it against their branch: the two notes agree on one checkpoint design, and
no note or code had to change.

| Design point | R2 says (and the code) | R1 says |
|---|---|---|
| Saver | One `PostgresSaver` on its own connection pool (`db.py`, `app.py`) | Same |
| Thread id | `thread_id` is the conversation id (`GraphWorkflow`, `GraphApprovalResumer`) | Same |
| Source of truth | The checkpoint decides where a workflow continues; `conversations.status` is only a copy | Same: its restart demo resumed from the checkpoint |
| In-memory saver | `InMemorySaver` only in `experiments/` | Same |
| State shape | The paused state is plain data (`ApprovalState`) | Same: with strict msgpack a dataclass came back as a dict (R1 finding 7) |

What R1 adds that supports this note: its findings 3 and 4 show that a new connection, saver and
graph on the same database still have the whole conversation, so the approval pause, which is one
more checkpoint, survives a restart for the same reason. This note's own experiment cannot show
that, because it uses `InMemorySaver`. Until #53 is merged, that claim rests on R1's branch
(`feature/CFD-32-mrsn001-checkpoint-research`), not on this one.
