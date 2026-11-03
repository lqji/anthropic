# API contract

All endpoints are under `/api/v1`, take and return JSON, and are documented live at `/docs`.
Every error has the body `{"code": "...", "detail": "...", "request_id": "..."}`.

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
