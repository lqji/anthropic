# ADR: Human approval is a LangGraph interrupt, resumed through the API

- Status: accepted
- Issue: CFD-28 (describes what CFD-14 and CFD-15 built)
- Research: `docs/research/R2-human-in-the-loop.md`, experiment `experiments/hitl/`

## Context

Cancelling an appointment, and booking one for today, must wait for a person at the front desk.
The wait can last long and the service can restart in the meantime. The decision arrives later,
from a different HTTP request, and must continue the exact conversation that asked.

## Decision

1. **The pause is a real `interrupt()`.** The node `await_approval` (`workflows/approval.py`)
   calls `interrupt({"approval_id": ...})` and does nothing else. It is checkpointed by
   `PostgresSaver`, with the conversation id as `thread_id`. There is no status flag standing in
   for the pause. (`conversations.status = awaiting_approval` is a copy used only to answer
   "still waiting" without loading the graph.)
2. **Side effects live in their own nodes.** A resume runs the waiting node again from its first
   line, so `request_approval` (creates the approval row) runs before the pause and
   `apply_decision` (changes the booking) after it. Both are safe to run twice: the approval row
   is reused if it exists, and the booking updates only change a booking that is still in the
   expected state.
3. **The decision is saved first, then applied.** `POST /api/v1/approvals/{id}/approve|reject`
   (`api/approvals.py`, `services/approval.py`) takes the conversation lock, records the decision
   with a conditional UPDATE so only one decision can win, then resumes the graph with
   `Command(resume={"decision": ..., "reason": ...})`, and stores the patient's message.
4. **A saved but unfinished decision is always finished.** If the process dies, or the resume
   fails, after the decision is saved:
   - repeating the same decision completes it (a different decision gets `409 already_decided`);
   - `reconcile()` runs at startup and resumes every approval that is decided with no
     `resumed_at`.
   `GraphApprovalResumer` looks at the checkpoint before it acts, so a decision is never applied
   twice and a graph that never reached its pause is brought there first.
5. **While paused, the patient gets "still waiting".** The conversation service answers routine
   messages with a fixed reply and does not run the graph. Emergencies are still checked first and
   answered.
6. **Database rules back the code.** One pending approval per booking and kind, and one per
   conversation (partial unique indexes); a foreign key to the booking; approvals are audit
   records and cannot be deleted with their conversation.

## Consequences

- A restart loses nothing: the pause, the approval and the half-finished resume are all in
  PostgreSQL.
- Approve and reject, and cancellation and same-day booking, share one path. A new approval kind
  adds a branch in `request_approval` and `apply_decision`, not a new mechanism.
- A pending approval never expires, staff are not authenticated, and the patient is not notified
  (they see the result in the chat). Each would be a separate issue.
- Conversation locks are in memory, so the service runs one worker.
- Nodes before `interrupt()` must stay free of side effects. A reviewer should reject a change
  that breaks this.

## Alternatives considered

- **Status flag in our tables.** Rejected: it records that something is waiting but not where to
  continue or with what state, so we would rebuild the checkpoint by hand. Forbidden by the
  project rules.
- **`interrupt_before` on a node.** Not chosen: the answer comes back through `update_state`
  rather than as the value of the question, and the question and the answer end up in different
  places in the code.
- **A timeout or polling inside the request.** Rejected: staff may take days; no HTTP request
  should wait for them.
