-- Audit list (CFD-30): what was booked, cancelled, approved or rejected, by whom and when.
-- Append-only. Idempotent: safe on every start.
--
-- A row is written in the same transaction as the change it describes (see the repositories), so
-- a failed change leaves no row and a successful one cannot lack it. No patient message text and
-- no patient reference: `actor` is the conversation id for a patient, the staff member's name for
-- a decision, or "workflow" for the booking change that follows a decision.

CREATE TABLE IF NOT EXISTS audit_events (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at               timestamptz NOT NULL,                -- from the injected Clock, never now()
    actor_type       text NOT NULL CHECK (actor_type IN ('patient', 'staff', 'system')),
    actor            text NOT NULL,
    action           text NOT NULL CHECK (action IN (
                         'booking_created', 'same_day_requested', 'cancellation_requested',
                         'approval_approved', 'approval_rejected',
                         'booking_confirmed', 'booking_cancelled', 'booking_rejected')),
    outcome          text NOT NULL,
    booking_id       bigint NOT NULL REFERENCES bookings (id) ON DELETE RESTRICT,
    conversation_id  uuid NOT NULL REFERENCES conversations (id) ON DELETE RESTRICT,
    reason           text                                 -- staff's reason on a decision, else NULL
);

CREATE INDEX IF NOT EXISTS audit_events_newest_idx ON audit_events (at DESC, id DESC);
CREATE INDEX IF NOT EXISTS audit_events_booking_idx ON audit_events (booking_id);
CREATE INDEX IF NOT EXISTS audit_events_conversation_idx ON audit_events (conversation_id);

-- Append-only in the database itself, not only in the code: no UPDATE and no DELETE.
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END
$$;

DROP TRIGGER IF EXISTS audit_events_no_change ON audit_events;
CREATE TRIGGER audit_events_no_change
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
