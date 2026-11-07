# Experiment: human in the loop (R2)

The smallest graph that proves LangGraph's `interrupt()` and `Command(resume=...)`. It uses the
project's `FakeLLM` (no API key) and `InMemorySaver` (allowed in `experiments/` only; the service
uses `PostgresSaver`). No database is needed.

## Run

```bash
uv run python experiments/hitl/hitl_demo.py
```

`tests/test_hitl_experiment.py` runs the same code in CI, so the experiment cannot go stale.

## What it prints

```text
1. paused; the caller got {'question': 'Cancel this appointment?', 'text': 'Cancel my appointment tomorrow'}
   next node: ('ask_staff',)
   reply so far: None (nothing yet: the patient has no answer)
2. another thread answered at once: 'Our front desk is open during clinic hours.'
3. a new graph sees the pause: next = ('ask_staff',)
4. resumed with 'approved': 'Your appointment has been cancelled.'
   ask_staff started 2 times (re-run on resume)
   next node after finishing: ()
5. resumed with 'rejected': 'Not approved: doctor needs this visit. Your appointment is still booked.'
```

## How to read it

1. A `cancel` message reaches `interrupt()`. `invoke` returns at once with the payload under
   `__interrupt__`, and `get_state(...).next` is the node that is waiting.
2. The pause belongs to one `thread_id`. Another conversation runs normally.
3. The waiting state is in the saver, not in the graph object. A second graph built on the same
   saver finds it. (With `InMemorySaver` a real restart loses it; `PostgresSaver` keeps it.)
4. `Command(resume={...})` continues the same thread. The value becomes the return value of
   `interrupt()`. The waiting node runs again from its first line, so everything before
   `interrupt()` must be safe to repeat.
5. The rejection takes the other branch of the same node.
