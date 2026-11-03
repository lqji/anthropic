# API contract

All endpoints are under `/api/v1`, take and return JSON, and are documented live at `/docs`.
Every error has the body `{"code": "...", "detail": "...", "request_id": "..."}`.

Every endpoint can return `503 database_unavailable` (logged): the database is unreachable, or it
is **not ready yet**. The api starts even when the database is down at start and creates its
tables in the background once the database is reachable; until then a request that needs a table
gets this 503, and the same request works afterwards. Retry after a few seconds.

## Conversations (CFD-06)

### `POST /api/v1/conversations`
Start a conversation.

- Body: `{"patient_ref": "demo-patient-1"}`. `patient_ref` is 1 to 200 characters after trimming.
- `201`: `{"id": "<uuid>", "patient_ref": "...", "status": "active", "created_at": "..."}`
- `422 validation_error`: `patient_ref` is missing or blank.

### `POST /api/v1/conversations/{id}/messages`
Send a patient message and get the assistant's reply.

- Body: `{"text": "hello"}`. `text` is 1 to 2000 characters after trimming.
- `200`: `{"reply": "...", "intent": "...", "outcome": "...", "status": "active", "sources": []}`
- `404 not_found`: unknown id (including an id that is not a UUID).
- `503 llm_unavailable`: the language model failed or timed out.
- `409 conversation_busy`: another message to the same conversation is still being handled.
- `422 validation_error`: `text` is empty, blank, or over 2000 characters.
- `503 workflow_unavailable`: the workflow returned a status the conversation cannot have.
- `503 database_unavailable`: see above. This includes the checkpoint tables not being ready yet.

`status` is the conversation status after the turn: `active`, `awaiting_approval` (CFD-14) or
`closed`.

**Emergencies (CFD-08).** If the message matches a deterministic emergency rule
(`domain/safety.py`), the reply is returned at once with `intent` and `outcome` both
`"emergency"`, and the text tells the patient to call `EMERGENCY_NUMBER`. This happens before the
conversation lock and without the workflow or the LLM, so it works even while another message is
being handled (no 409) or the LLM is down (no 503). The conversation status is unchanged, and the
message is flagged for staff (see below).

The instruction needs no database. If the database is down or saving fails, the patient still gets
the instruction (status 200), nothing is stored (the save is one transaction) and the error is
logged as `emergency.persist_failed` with the rule name only. In that case the text says
"Please also phone the clinic directly" instead of "Our front-desk staff have been alerted",
because no staff flag was saved. A conversation id that does not exist is still `404 not_found`.

`sources` (CFD-11) lists the clinic document sections a clinic question was answered from, as
`[{"document": "visit-preparation.md", "heading": "Blood tests"}]`, and the reply ends with
`Source: <document>`. It is `[]` for every other reply, including `outcome: "not_in_documents"`
(the question is not covered by the clinic's documents).

### `GET /api/v1/conversations/{id}`
Read a conversation back, with all its messages oldest first.

- `200`: the conversation fields plus
  `"messages": [{"role": "patient" | "assistant", "text": "...", "intent": ..., "outcome": ..., "created_at": "..."}]`.
  `intent` and `outcome` are set on assistant messages only.
- `404 not_found`: unknown id.

## Emergencies (CFD-08)

### `GET /api/v1/emergencies?status=open`
Emergency flags for front-desk staff, newest first.

- `status`: `open` (default) or `acknowledged`.
- `200`: `[{"id": 1, "conversation_id": "<uuid>", "matched_rule": "chest_pain", "excerpt": "...", "status": "open", "created_at": "..."}]`.
  `excerpt` is the first 200 characters of the patient's message.
- `422 validation_error`: any other `status`.

## Approvals (CFD-14)

A cancellation (and, from CFD-15, a same-day booking) waits for front-desk staff. The message
that asks for it gets `status: "awaiting_approval"` and an `approval_id`. The conversation is then
paused: every non-emergency message gets a "still waiting" reply (`outcome: "still_waiting"`)
until staff decide, while emergencies are still answered. The pause survives a restart. If the
process dies right after the approval is saved and before the pause is checkpointed, the approval
is still pending; when staff decide, the workflow is brought to its pause first and the decision
is then applied once (no second approval is created).

### `GET /api/v1/approvals?status=pending`
Approvals for staff, oldest first, one page at a time.

- `status`: `pending` (default), `approved` or `rejected`.
- `limit`: page size, 1 to 500 (default 100). `offset`: how many to skip (default 0). To read
  everything, ask for the next page (`offset += limit`) until a page comes back shorter than `limit`.
- `200`: `[{"id": "<uuid>", "conversation_id": "<uuid>", "kind": "cancellation", "booking_id": 7, "status": "pending", "decided_by": null, "reason": null, "created_at": "...", "decided_at": null}]`
- `422 validation_error`: any other `status`, or a `limit` or `offset` out of range.

Approvals are audit records: deleting a conversation that has approvals is refused by the database
(`ON DELETE RESTRICT`).

### `POST /api/v1/approvals/{id}/approve` and `POST /api/v1/approvals/{id}/reject`
Decide an approval. The paused conversation resumes and the patient is told the result.

- Body: `{"decided_by": "desk-1", "reason": "..."}`. `decided_by` is 1 to 100 characters and
  `reason` is 3 to 500 characters, both after trimming.
- `200`: `{"approval_id": "<uuid>", "status": "approved" | "rejected", "patient_message": "..."}`.
  `patient_message` is also stored as an assistant message in the conversation.
- `404 not_found`: unknown id.
- `409 already_decided`: the approval was already approved or rejected and the conversation has
  resumed, or it was decided the other way. A decision is final once saved.
- `503 resume_failed`: the decision **is saved**, but resuming the conversation failed (logged
  as `approval.resume_failed`). The conversation stays `awaiting_approval`. Repeat the **same**
  call (the same decision) to finish the job: the saved `decided_by` and `reason` are kept, and
  the decision is applied once. A different decision gets `409 already_decided`. If nobody
  retries, the conversation is resumed at the next service start.
- `409 conversation_busy`: the conversation is handling a message right now. The approval stays
  pending; try again.
- `422 validation_error`: blank `decided_by` or a `reason` under 3 characters. The approval stays
  pending.

## Appointments (CFD-12)

Times are returned in clinic time (`CLINIC_TIMEZONE`) with their UTC offset, e.g.
`2026-11-03T09:00:00+04:00`.

### `GET /api/v1/specialties`
- `200`: `[{"id": 1, "name": "Dermatology"}, ...]`, by name.

### `GET /api/v1/doctors?specialty=`
- `specialty` (optional, case-insensitive): only the doctors of that specialty.
- `200`: `[{"id": 1, "name": "Dr. Salma Nasser", "specialty": "Dermatology"}, ...]`, by name.
- `404 not_found`: unknown specialty.

### `GET /api/v1/slots?date=&doctor_id=&specialty=`
Free slots that have not started yet, ordered by time.

- `date` (required): `YYYY-MM-DD`, a day in clinic time, today or later.
- `doctor_id` and/or `specialty`: at least one is required.
- `200`: `[{"id": 7, "doctor_id": 1, "doctor": "Dr. Salma Nasser", "specialty": "Dermatology", "starts_at": "...", "ends_at": "..."}]`
- `422 validation_error`: no `doctor_id` or `specialty`; `date` missing, not a date, or in the past.
- `404 not_found`: unknown `doctor_id` or `specialty`.
