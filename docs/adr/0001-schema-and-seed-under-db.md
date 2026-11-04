# ADR 0001: Schema and seed SQL live under `db/` and the app applies them

Status: accepted (CFD-25)

## Context

The brief's compose package lists `db/` with the schema and seed SQL. Before this change the schema
was packaged inside the Python package (`src/clinic/sql/`) and the seed was Python data in
`src/clinic/seed.py`. Nothing was under `db/`.

PostgreSQL's `/docker-entrypoint-initdb.d` runs its scripts only when the data directory is empty,
that is, on a brand new volume. The deployed VM already has a `pgdata` volume with the old schema,
and the runbook says a normal deploy keeps that volume.

## Decision

- `db/schema/NN_*.sql` is the schema, moved unchanged from `src/clinic/sql/` (same files, same
  order). `db/seed/NN_*.sql` is the demo catalogue (2 specialties, 2 doctors).
- The app applies both on start, as it applied the schema before. The image copies `db/` to
  `/app/db`; `DB_DIR` (default `db`, relative to the working directory like `CLINIC_DOCS_DIR`)
  points at it. A missing folder stops startup with a message that names `DB_DIR`.
- Compose does **not** mount `db/` into `/docker-entrypoint-initdb.d`.
- Slots stay in Python (`clinic.seed`). They depend on the clinic time zone, the working hours and
  the injected `Clock` ("time-dependent SQL takes `now` from the Clock"), and the working-hours
  rules already live in `clinic.domain.schedule`. Moving them into SQL would copy those rules.
  Slots are built for the doctor ids read back from the database, so the doctor list exists only
  in `db/seed/01_catalogue.sql`.
- Clinic documents stay Markdown in `seed/clinic_docs/`, loaded and chunked by the app
  (`KnowledgeLoader`, which skips unchanged files by sha256). Turning them into SQL inserts would
  copy the chunking logic.
- LangGraph checkpoint tables are still created by `PostgresSaver.setup()`.

## Why not initdb.d

| | App applies `db/` (chosen) | Mount `db/` in initdb.d |
|---|---|---|
| Fresh volume | works | works |
| Existing volume (the VM) | works: every file is idempotent, so it runs on every start | does not run; the app would still need to apply the schema, so there would be two paths running the same files |
| Restart | same files run again, nothing changes | not run again |
| Slot dates | from the injected `Clock` | `CURRENT_DATE` of the database server, outside the Clock |
| Failure | api start fails or retries and logs | the database container fails to initialise |

## Idempotency

- Schema files use `CREATE ... IF NOT EXISTS` and run on every start (unchanged behaviour).
- The catalogue and slots run once, in one transaction, only while the `doctors` table is empty.
  Its inserts also use `ON CONFLICT DO NOTHING`. A restart or an existing volume with doctors
  changes nothing; a failed seed rolls back and is retried at the next start.

## Consequences

- One source of truth: the DDL exists only in `db/schema/`; there is no second copy.
- The "Compose package" deliverable is met by the `db/` folder in the repository and in the image.
  A new machine gets schema, doctors, slots and documents from `docker compose up --build -d --wait`
  with no manual step.
- Running `db/` by hand with `psql` works for the schema and the catalogue; slots need the app.
