-- Clinic documents and their searchable chunks (CFD-10). Idempotent: safe to run on every start.

CREATE TABLE IF NOT EXISTS documents (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name    text NOT NULL UNIQUE,                      -- file name, e.g. visit-preparation.md
    sha256  text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal      integer NOT NULL CHECK (ordinal >= 0), -- position of the chunk in its document
    heading      text NOT NULL,                         -- '' for text before the first heading
    content      text NOT NULL CHECK (content <> ''),
    tsv          tsvector GENERATED ALWAYS AS (
                     setweight(to_tsvector('english', heading), 'A')
                     || setweight(to_tsvector('english', content), 'B')
                 ) STORED,
    UNIQUE (document_id, ordinal)
);

CREATE INDEX IF NOT EXISTS doc_chunks_tsv_idx ON doc_chunks USING gin (tsv);
