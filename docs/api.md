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
- `200`: `{"reply": "...", "intent": "...", "outcome": "...", "status": "active"}`
- `404 not_found`: unknown id (including an id that is not a UUID).
- `409 conversation_busy`: another message to the same conversation is still being handled.
- `422 validation_error`: `text` is empty, blank, or over 2000 characters.
- `503 workflow_unavailable`: the workflow returned a status the conversation cannot have.
- `503 database_unavailable`: see above. This includes the checkpoint tables not being ready yet.

`status` is the conversation status after the turn: `active`, `awaiting_approval` (CFD-14) or
`closed`.

### `GET /api/v1/conversations/{id}`
Read a conversation back, with all its messages oldest first.

- `200`: the conversation fields plus
  `"messages": [{"role": "patient" | "assistant", "text": "...", "intent": ..., "outcome": ..., "created_at": "..."}]`.
  `intent` and `outcome` are set on assistant messages only.
- `404 not_found`: unknown id.

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
