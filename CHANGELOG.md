# Changelog

All notable changes to the Clinic Front-Desk Assistant. Versions follow [Semantic Versioning](https://semver.org/); a release is the Git tag `vX.Y.Z`.

## [0.1.0] - 2026-11-03

First release: a REST API that works as a clinic's front desk.

### Added
- **Conversations** (CFD-05, CFD-06): start a conversation, send messages, read it back, and resume it by id. The workflow is a LangGraph graph checkpointed in PostgreSQL, so a conversation survives a restart.
- **Intent and routing** (CFD-04, CFD-07): the model classifies each message as a clinic question, finding slots, booking, cancelling or other, and asks a question when it is unsure. The model sits behind an `LLMPort`; tests and demos use a deterministic `FakeLLM`.
- **Answers from the clinic's own documents** (CFD-10, CFD-11): PostgreSQL full-text search over the Markdown documents. The reply names its source, or says the question is not covered and suggests the front desk. The model is not called when nothing matches.
- **Appointments** (CFD-12, CFD-13): doctors and free slots by doctor, specialty and day; booking a later-day slot at once. Double booking is impossible in SQL (a conditional update plus a unique index).
- **Staff approval** (CFD-14, CFD-15): a cancellation and a same-day booking pause the conversation for the front desk through a real LangGraph `interrupt()`. Staff approve or reject through the API. The pause survives a restart, and a decision stored before a crash is applied at the next start.
- **Safety** (CFD-08, CFD-09): emergency messages are recognised by deterministic rules before any model call, answered at once with the emergency number, and flagged for staff. The assistant never diagnoses or recommends treatment (a prompt rule plus an output guard).
- **Operations** (CFD-01, CFD-03, CFD-16): Docker Compose with a health check, fail-fast configuration, JSON errors with request ids, `scripts/smoke_test.sh`, and a README runbook for deploy, rollback and restore on an Ubuntu VM.

### Known issues
- No authentication on `/api/v1`: run it on a trusted network or behind an authenticating proxy.
- An emergency flag cannot be closed through the API.
- Staff can approve a held same-day slot after its start time has passed; nothing checks it again. A held slot stays held until staff decide. A pending approval never expires.
- "Book number 2" is read by taking the first number in the message, and a booking is made without a confirmation step. A patient can hold two bookings at the same time.
- The demo slots are seeded once, for 5 days.
- English only.

[0.1.0]: https://github.com/lqji/anthropic/releases/tag/v0.1.0
