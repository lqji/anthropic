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

### `GET /api/v1/conversations/{id}`
Read a conversation back, with all its messages oldest first.

- `200`: the conversation fields plus
  `"messages": [{"role": "patient" | "assistant", "text": "...", "intent": ..., "outcome": ..., "created_at": "..."}]`.
  `intent` and `outcome` are set on assistant messages only.
- `404 not_found`: unknown id.
