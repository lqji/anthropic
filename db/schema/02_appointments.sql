-- Doctors, appointment slots and bookings (CFD-12). Idempotent: safe to run on every start.

CREATE TABLE IF NOT EXISTS specialties (
    id    integer PRIMARY KEY,                          -- fixed ids from the seed
    name  text NOT NULL UNIQUE CHECK (btrim(name) <> '')
);

CREATE TABLE IF NOT EXISTS doctors (
    id            integer PRIMARY KEY,                  -- fixed ids from the seed
    name          text NOT NULL UNIQUE CHECK (btrim(name) <> ''),
    specialty_id  integer NOT NULL REFERENCES specialties (id)
);

CREATE TABLE IF NOT EXISTS slots (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    doctor_id  integer NOT NULL REFERENCES doctors (id),
    starts_at  timestamptz NOT NULL,
    ends_at    timestamptz NOT NULL,
    status     text NOT NULL DEFAULT 'free' CHECK (status IN ('free', 'held', 'booked')),
    CHECK (ends_at > starts_at),
    UNIQUE (doctor_id, starts_at)
);

CREATE INDEX IF NOT EXISTS slots_free_starts_idx ON slots (starts_at) WHERE status = 'free';

CREATE TABLE IF NOT EXISTS bookings (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slot_id          bigint NOT NULL REFERENCES slots (id),
    conversation_id  uuid NOT NULL REFERENCES conversations (id),
    status           text NOT NULL
                     CHECK (status IN ('pending_approval', 'confirmed', 'cancelled', 'rejected')),
    created_at       timestamptz NOT NULL,                -- from the injected Clock
    updated_at       timestamptz NOT NULL                 -- from the injected Clock
);

-- At most one active booking per slot, enforced by PostgreSQL itself (CFD-13 relies on this).
CREATE UNIQUE INDEX IF NOT EXISTS bookings_one_active_per_slot
    ON bookings (slot_id) WHERE status IN ('pending_approval', 'confirmed');

CREATE INDEX IF NOT EXISTS bookings_conversation_idx ON bookings (conversation_id);
