# API contract

All endpoints are under `/api/v1`, take and return JSON, and are documented live at `/docs`.
Every error has the body `{"code": "...", "detail": "...", "request_id": "..."}`.

## Conversations (CFD-06)

### `POST /api/v1/conversations`
Start a conversation.

- Body: `{"patient_ref": "demo-patient-1"}`. `patient_ref` is 1 to 200 characters after trimming.
- `201`: `{"id": "<uuid>", "patient_ref": "...", "status": "active", "created_at": "..."}`
- `422 validation_error`: `patient_ref` is missing or blank.

### `POST /api/v1/conversations/{id}/messages`
Send a patient message and get the assistant's reply.

- Body: `{"text": "hello"}`. `text` is 1 to 2000 characters after trimming.
- `200`: `{"reply": "...", "intent": "...", "outcome": "...", "status": "active"}`
- `404 not_found`: unknown id (including an id that is not a UUID).
- `409 conversation_busy`: another message to the same conversation is still being handled.
- `422 validation_error`: `text` is empty, blank, or over 2000 characters.
- `503 workflow_unavailable`: no conversation workflow is wired in yet (until CFD-05).

`status` is the conversation status after the turn: `active`, `awaiting_approval` (CFD-14) or
`closed`.

**Emergencies (CFD-08).** If the message matches a deterministic emergency rule
(`domain/safety.py`), the reply is returned at once with `intent` and `outcome` both
`"emergency"`, and the text tells the patient to call `EMERGENCY_NUMBER`. This happens before the
conversation lock and without the workflow or the LLM, so it works even while another message is
being handled (no 409) or the LLM is down (no 503). The conversation status is unchanged, and the
message is flagged for staff (see below).

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
until staff decide, while emergencies are still answered. The pause survives a restart.

### `GET /api/v1/approvals?status=pending`
Approvals for staff, oldest first.

- `status`: `pending` (default), `approved` or `rejected`.
- `200`: `[{"id": "<uuid>", "conversation_id": "<uuid>", "kind": "cancellation", "booking_id": 7, "status": "pending", "decided_by": null, "reason": null, "created_at": "...", "decided_at": null}]`
- `422 validation_error`: any other `status`.

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
