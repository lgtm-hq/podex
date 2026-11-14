# 0007. PostgreSQL test lane with a migrated database per test

- Status: proposed
- Date: 2026-11-14
- Tags: testing, ci, database

## Context

The backend suite runs on in-memory SQLite created from `Base.metadata`.
Production runs PostgreSQL with pgvector, and several behaviours exist only
there: row locks (`FOR UPDATE`, `SKIP LOCKED`), concurrent writers blocking on
unique indexes, timezone-aware timestamps and the migrated schema itself. The
only PostgreSQL CI job replays migrations. PostgreSQL concurrency tests for
media merges existed but no job ran them, because they skipped themselves unless
`PODEX_DATABASE__URL` was a PostgreSQL URL. Upcoming billing, quota, webhook and
vector-search work needs a place where such behaviour is proven.

## Decision

Tests that need PostgreSQL carry `@pytest.mark.postgres`, live under
`backend/tests/postgres/` and use the `postgres` fixture. One hook in
`backend/tests/conftest.py` is the only availability mechanism: when
`PODEX_DATABASE__URL` is not a PostgreSQL URL, marked tests are skipped with the
reason, or fail when pytest runs with `--require-postgres`.

The fixture gives each test its own database. Each pytest process creates a
uniquely named template database and migrates it with `alembic upgrade head`
once; each test gets a copy made with `CREATE DATABASE ... TEMPLATE`, dropped
`WITH (FORCE)` in a `finally` block, as is the template at session end. The
database named in the URL is used only to create and drop databases, so the
fixture never reads or writes a table it did not create, and random names keep
parallel workers and concurrent runs apart. We chose migrated databases over
`Base.metadata.create_all` so the lane tests the schema production runs, and
databases over schemas because `CREATE EXTENSION vector` and the Alembic version
table are per database, which makes schema-level isolation leaky.

Races use `tests/postgres/concurrency.py`: the second transaction runs on its
own connection and the first commits only after `pg_stat_activity` shows the
second waiting on a lock. No sleeps decide ordering.

CI runs `pytest tests -m postgres --require-postgres` in the
`🐘 Postgres Application Tests` job against the same digest-pinned pgvector
image as the migration replay, and the combined test gate includes its result.
The lane does not collect coverage; the 85% floor stays on the SQLite coverage
job. We deliberately do not run the whole SQLite suite on PostgreSQL.

## Consequences

- Feature work that depends on PostgreSQL semantics adds its regressions to
  `backend/tests/postgres/` with the marker; they run in CI and skip locally
  without a PostgreSQL URL.
- The database role in `PODEX_DATABASE__URL` needs `CREATEDB` to run the lane
  locally.
- Each test pays for one `CREATE DATABASE` from a template; each process pays
  for one migration replay.
- Code exercised only by PostgreSQL tests does not count toward coverage.
