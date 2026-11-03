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

The instruction needs no database. If the database is down or saving fails, the patient still gets
the instruction (status 200), nothing is stored (the save is one transaction) and the error is
logged as `emergency.persist_failed` with the rule name only. A conversation id that does not
exist is still `404 not_found`.

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
