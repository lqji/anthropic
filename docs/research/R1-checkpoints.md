# R1: Checkpoints

Issue: CFD-32. Owner: MrSn001. Experiment: `experiments/checkpoints/`. Decision:
`docs/adr/checkpoint-design.md`.

## Question

How do we keep each conversation's workflow state under its own thread ID, and which checkpointer
survives a container restart?

## What we need

A patient's conversation is spread over many HTTP requests. Between them the workflow has to
remember things that are not in the request: the earlier messages, the slots it offered ("book the
first one" refers to them), and whether it is waiting for staff. Two patients must never see each
other's state. The api runs in a container that is restarted on every deploy and can crash at any
time, so whatever holds this state has to live outside the api process.

## How LangGraph does it

A compiled graph takes a checkpointer. Every call names a `thread_id` in its config. After each
step the checkpointer saves the whole state of that thread as a new checkpoint. The next call with
the same `thread_id` starts from the latest one; a call with another `thread_id` starts empty. So
"one conversation" is simply "one thread id", and the question becomes where the checkpointer
writes.

## Options compared

| | A. `InMemorySaver` | B. `SqliteSaver` | C. `PostgresSaver` |
|---|---|---|---|
| Where the state lives | A dict inside the Python process | A file on the api container's disk | Tables in our PostgreSQL |
| Survives a restart | No: the experiment lost everything (step 1) | Only if the file is on a volume | Yes: the experiment kept everything (step 3) |
| Package | Part of `langgraph` | `langgraph-checkpoint-sqlite` (not installed) | `langgraph-checkpoint-postgres` (installed) |
| Shared by several processes | No | Poorly: one file, one writer at a time | Yes |
| Backed up with the clinic data | No | No, a second thing to back up | Yes, the same `pg_dump` |
| Fit with `AGENTS.md` | Allowed in `experiments/` only | Forbidden ("No SQLite") | Required |

A and C were run. B was compared on paper: its package is not a dependency, and the project rules
forbid SQLite, so I did not add it just to measure it. Even without the rule it would mean a second
database file to mount, back up and restore, next to the PostgreSQL we already run.

## Sources

- LangGraph, persistence (threads, checkpoints, checkpointer libraries):
  <https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph, memory (short-term memory and trimming messages):
  <https://docs.langchain.com/oss/python/langgraph/add-memory>
- LangGraph, durable execution: <https://docs.langchain.com/oss/python/langgraph/durable-execution>
- `langgraph-checkpoint-postgres`: <https://pypi.org/project/langgraph-checkpoint-postgres/>,
  source <https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres>
- `langgraph-checkpoint-sqlite`: <https://pypi.org/project/langgraph-checkpoint-sqlite/>

Versions tested: `langgraph` 1.2.19, `langgraph-checkpoint` 4.3.1, `langgraph-checkpoint-postgres`
3.2.0, PostgreSQL 16.

## What the experiment showed

`experiments/checkpoints/checkpoints_demo.py` has one node that classifies the message with
`FakeLLM` and replies with how many earlier messages it can see. Run it with `DATABASE_URL=...
uv run python experiments/checkpoints/checkpoints_demo.py`; the README there has the full output.

1. **Thread ids keep conversations apart.** Anna's second message could see her first exchange;
   Omar's first message could see nothing. That held in memory and in Postgres.
2. **`InMemorySaver` does not survive a restart.** A new saver (what a new process gets) had 0
   messages for Anna.
3. **`PostgresSaver` does.** With a new connection, a new saver and a new graph, Anna still had
   her 4 messages, and her third message replied "I can see 4 earlier messages". The only thing
   that was not new was the database.
4. **The real container restart behaves the same.** `restart_demo.sh` asked the running api for
   Dr. Salma's slots, ran `docker compose restart api`, and then "book the first one" booked the
   first slot of that list. The list was not in the new api process, so it came from the
   checkpoint.
5. **The tables grow on every turn and never shrink.** `setup()` created four tables
   (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`). Each turn of
   this one-node graph added 3 rows to each of the first three. The stored blobs grew faster than
   the turns: 840 bytes after 1 turn, 2,586 after 2, 69,000 after 12, because each saved blob
   holds the whole message list again. Nothing deletes old checkpoints.
6. **The checkpoint keeps the whole history; the prompt does not need it.** After 12 turns the
   state held 24 messages, and the service's `history_window` gave the model the last 10,
   starting with a patient message.
7. **Strict msgpack silently changes non-plain values.** A dataclass put into the state came back
   after a reconnect as a plain `dict` with the strict serializer
   (`LANGGRAPH_STRICT_MSGPACK=true`), and as the dataclass with the permissive one. LangGraph only
   logged a line. Code that reads `slot.doctor` would work until the first restart and then
   fail. LangChain messages are on the allow list and loaded fine in strict mode.

Findings 3 and 4 answer the question. Finding 7 is the one that shapes how the state is written.

## What the service does today

- `GraphWorkflow` runs every turn with `thread_id` = the conversation id, so the conversation
  table and the checkpoints share one key.
- The graph's `PostgresSaver` gets its own connection pool (`db.make_checkpoint_pool`): autocommit
  and dict rows, as the saver requires, kept apart from the app pool so the repositories keep their
  one-block-one-transaction behaviour.
- `setup_checkpointer` runs `PostgresSaver.setup()` on every start, on its own autocommit
  connection (the migrations create indexes concurrently, which cannot run in a transaction) with a
  5-minute statement timeout instead of the 5-second request timeout.
- `ConversationState` holds plain values and LangChain messages only (`workflows/state.py`), and
  `.env.example`, the README and CI set `LANGGRAPH_STRICT_MSGPACK=true`.
- Prompts get `history_window(messages, HISTORY_WINDOW)`; the checkpoint keeps every message.
- Each turn's messages get ids from the stored turn number (`turn_id_for`), so a retried turn
  replaces itself in the checkpoint instead of adding a duplicate.

## Recommendation

Use `PostgresSaver` on the clinic's own PostgreSQL, with `thread_id` = conversation id, as the
code does. Keep the state plain data and keep strict msgpack on, so a mistake shows up as a wrong
type in a test and not after a restart in production. Trim what goes to the model, not what is
stored. ADR `checkpoint-design` records this.

## Risks and known limitations

- **Checkpoint tables only grow.** Every turn adds rows and a copy of the message list, and
  nothing prunes them. For a demo clinic that is fine; a real one needs a retention rule (for
  example, keep the latest checkpoint per closed conversation) as a separate issue. They are also
  in every backup.
- **The tables belong to LangGraph.** `setup()` creates and migrates them. An upgrade of
  `langgraph-checkpoint-postgres` can run a migration at start; that is why setup has the long
  statement timeout. Our SQL never writes to them.
- **Strict msgpack depends on the environment.** The setting is read from
  `LANGGRAPH_STRICT_MSGPACK` when LangGraph is imported; the app does not check it. A deployment
  whose `.env` drops the line runs permissive, and LangGraph says it will block unknown types in a
  future version anyway. Plain-data state avoids both.
- **Bounded history window.** The model only sees the last `HISTORY_WINDOW` messages (10). A
  patient who refers to something said earlier than that is not understood by the model, even
  though the checkpoint still has it. Facts that must outlive the window (offered slots, the
  pending approval) are kept as their own state fields, not left in the messages.
- **One process.** The checkpoint survives restarts, but the conversation lock that stops two
  turns of one conversation running at once is in memory, so the api runs one worker.

## Agreement with R2 (CFD-28)

R2 (`docs/research/R2-human-in-the-loop.md`, PR #52) lists what it assumes about checkpoints.
Compared line by line with this note and with the code:

| R2 assumes | R1 | Code |
|---|---|---|
| One `PostgresSaver` on its own connection pool | Same (finding 3, "What the service does today") | `app.py`, `db.make_checkpoint_pool` |
| `thread_id` = conversation id | Same | `services/graph_workflow.py` |
| The checkpoint is the source of truth for where the workflow is | Same: the restart demo resumed from it | `GraphApprovalResumer` reads `get_state` before acting |
| `InMemorySaver` only in `experiments/` | Same | Nothing in `src/` imports it |
| The paused state must be plain data | Same, and finding 7 shows what happens otherwise | `workflows/state.py`, `ApprovalState` |

The two notes describe one checkpoint design. R2's pause survives a restart for the reason R1
measured in findings 3 and 4.
