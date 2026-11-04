# Experiment: checkpoints (R1)

The smallest graph that answers the R1 question: how does each conversation keep its own workflow
state, and which checkpointer still has it after a restart? It uses the project's `FakeLLM` (no
API key). `InMemorySaver` is allowed in `experiments/` only; the service uses `PostgresSaver`.

The graph has one node, `respond`. It classifies the latest patient message and replies with how
many earlier messages it can see, so every reply shows how much of its own conversation the graph
remembers.

## Run

It needs a PostgreSQL. The dev database works (`docker compose -f compose.yaml -f compose.dev.yaml
up -d db`):

```bash
DATABASE_URL=postgresql://clinic:change-me-local-only@localhost:5432/clinic \
  uv run python experiments/checkpoints/checkpoints_demo.py
```

It uses new thread ids on every run, so it can run again on the same database. It only adds rows
to the four checkpoint tables. `tests/test_checkpoints_experiment.py` runs it in CI against a
fresh database, so it cannot go stale.

## Demo: restart the api in the middle of a conversation

`restart_demo.sh` does it on the real stack. Run it from the repository root while the app is up
(`docker compose up --build -d --wait`):

```bash
experiments/checkpoints/restart_demo.sh            # or pass another base URL
```

It starts a conversation, asks for Dr. Salma's free slots tomorrow, runs `docker compose restart
api`, checks the api process really restarted, then sends "book the first one". The booking can
only be right if the api still has the slot list it showed before the restart, and that list is
in the conversation's checkpoint (`offered_slots`), not in the api process. It books one real
slot for the patient `restart-demo`, so use a demo database. Output from a run in demo mode:

```text
1. conversation 746ee9af-8358-4a1b-b5a8-093472268b83
2. patient: When is Dr. Salma free tomorrow?
  [slots_listed] Free slots with Dr. Salma Nasser on Thursday 5 November:
1. 09:30
...
3. restarting the api container (docker compose restart api)
   api process started at 2026-11-04T07:16:08.357122076Z, now 2026-11-04T07:16:14.727099603Z
4. patient: book the first one
  [booked] You're booked with Dr. Salma Nasser on Thursday 5 November at 09:30. See you then!
5. the stored conversation:
  ...
OK: the conversation continued after the restart
```

## What the experiment prints

```text
1. memory, anna turn 2: '[clinic_question] I can see 2 earlier messages.'
   memory, omar turn 1: '[book] I can see 0 earlier messages.'
   held: anna=4, omar=2
   after restart: anna=0 (state gone)
2. postgres, anna turn 1: rows checkpoints=3, checkpoint_blobs=3, checkpoint_writes=3, blob bytes=840
   postgres, anna turn 2: rows checkpoints=6, checkpoint_blobs=6, checkpoint_writes=6, blob bytes=2586
   held: anna=4, omar=2
   tables: checkpoint_blobs, checkpoint_migrations, checkpoint_writes, checkpoints
3. after restart: anna=4, omar=2
   anna turn 3: '[cancel] I can see 4 earlier messages.'
4. after 12 turns: checkpoint holds 24 messages, the prompt window 10 (first is human)
   rows checkpoints=36, checkpoint_blobs=36, checkpoint_writes=36, blob bytes=69000
5. dataclass after restart, strict: dict
   dataclass after restart, permissive: Slot
```

The two lines about `Slot` that LangGraph logs before this output are expected: they come from
step 5.

## How to read it

1. **One thread id, one conversation.** Anna's second message sees her first exchange (2 earlier
   messages). Omar's first message sees nothing of Anna's. A new `InMemorySaver` stands for a
   restart: Anna's state is gone.
2. **Postgres, same behaviour.** `setup()` creates four tables. Every turn adds 3 rows to each of
   the first three: LangGraph saves a checkpoint for the input, for the start of the step and
   after `respond`.
3. **A real restart keeps the state.** The connection, the saver and the graph are all new, yet
   Anna still has 4 messages and her third message continues the conversation. Both threads also
   used the strict serializer, so LangChain messages are fine under
   `LANGGRAPH_STRICT_MSGPACK=true`.
4. **The checkpoint keeps everything; the prompt does not.** After 12 turns the state holds all 24
   messages, and `history_window` (the service's own function) gives the model only the last 10,
   starting with a patient message. Storage grows faster than the conversation: each saved blob
   holds the whole message list, so 12 turns took 69,000 bytes, against 2,586 after 2 turns.
   Nothing deletes old checkpoints.
5. **Strict msgpack changes what comes back.** A dataclass stored in the state comes back as a
   plain `dict` with the strict serializer, and as the dataclass with the permissive one (which
   logs a warning that this will be blocked later). Code written against the dataclass would work
   until the first restart and then fail. This is why the service's state holds plain data only.
