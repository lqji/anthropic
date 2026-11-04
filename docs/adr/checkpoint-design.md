# ADR: Conversation state is checkpointed in PostgreSQL, one thread per conversation

- Status: accepted
- Issue: CFD-32 (describes what CFD-05 and CFD-06 built)
- Research: `docs/research/R1-checkpoints.md`, experiment `experiments/checkpoints/`
- Related: `docs/adr/approval-design.md` (R2, PR #52) relies on this design for its pause

## Context

A conversation is many HTTP requests. Between them the workflow must remember the earlier
messages, the slots it offered and whether it waits for staff. Conversations must not see each
other's state. The api container restarts on every deploy and may crash, so the state cannot live
in the process. PostgreSQL is the project's only database.

## Decision

1. **`PostgresSaver` on the clinic database.** The graph is compiled with a `PostgresSaver`
   (`app.py`). `InMemorySaver` is used only in `experiments/`.
2. **One thread per conversation.** `GraphWorkflow` (`services/graph_workflow.py`) invokes the
   graph with `thread_id` = the conversation id. The conversation row and its checkpoints share
   that key.
3. **The saver has its own pool.** `db.make_checkpoint_pool` opens autocommit connections with
   dict rows and `prepare_threshold=0`, which the saver needs, and the 5-second request statement
   timeout. It is separate from the app pool, so repositories keep one transaction per
   connection block.
4. **Tables are created on every start.** `setup_checkpointer` (`workflows/checkpointer.py`) runs
   `PostgresSaver.setup()` on its own autocommit connection (its migrations create indexes
   concurrently) with the 5-minute setup statement timeout. It is idempotent. If the database is
   down at start, the startup retry runs it later.
5. **The state is plain data.** `ConversationState` (`workflows/state.py`) holds strings, numbers,
   booleans, lists and dicts of them, and LangChain messages. `LANGGRAPH_STRICT_MSGPACK=true` is
   set in `.env.example`, the README and CI, so anything else would not load back as itself.
6. **Store everything, send a window.** The checkpoint keeps every message. Prompts get
   `history_window(messages, HISTORY_WINDOW)` (`workflows/history.py`, default 10). Facts that
   must outlive the window, such as `offered_slots` and the approval, are separate state fields.
7. **Turn ids make retries safe.** Each turn's message ids come from the stored turn number
   (`turn_id_for`), so a turn retried after a failed store replaces itself in the checkpoint.

## Consequences

- A restart or redeploy loses no conversation: the next message continues from the latest
  checkpoint (shown by `experiments/checkpoints/restart_demo.sh`). The approval pause (R2) is just
  another checkpoint and survives the same way.
- Workflow state is in the same database and the same backup as the clinic data.
- The checkpoint tables are owned and migrated by LangGraph; our SQL does not write to them.
- They grow with every turn and nothing prunes them. Retention is a separate issue.
- Adding a state field means plain data only; a reviewer should reject a dataclass or model in
  the state.
- The api still runs one worker, because the per-conversation lock is in memory, not because of
  the checkpointer.

## Alternatives considered

- **`InMemorySaver`.** Rejected: the experiment showed a new process starts with no state.
- **`SqliteSaver`.** Rejected: a second database file to mount, back up and restore, a single
  writer, and forbidden by the project rules.
- **Our own tables for conversation state.** Rejected: we would rebuild what a checkpoint already
  stores (the position in the graph, the state of every node) and lose LangGraph's interrupt and
  resume, which R2 depends on.
