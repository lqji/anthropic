-- Emergency flags for front-desk staff (CFD-08). Idempotent: safe to run on every start.

CREATE TABLE IF NOT EXISTS emergencies (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id  uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    matched_rule     text NOT NULL,                       -- rule name from domain/safety.py
    excerpt          text NOT NULL CHECK (char_length(excerpt) BETWEEN 1 AND 200),
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged')),
    created_at       timestamptz NOT NULL                 -- from the injected Clock
);

CREATE INDEX IF NOT EXISTS emergencies_status_idx ON emergencies (status, created_at DESC);
