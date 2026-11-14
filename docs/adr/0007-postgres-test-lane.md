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
reason, or fail when pytest runs with `--require-postgres`. Collection fails for
any test in that directory without the marker. The check runs as each test is
collected (`pytest_itemcollected`), before `-m` filtering or any
`pytest_collection_modifyitems` hook, including one in a nested conftest, can
deselect it. The SQLite fixtures fail there, so a test cannot land on the wrong
database or be deselected out of the lane.

The fixture gives each test its own database. Each pytest process creates a
uniquely named template database and migrates it with `alembic upgrade head`
once; each test gets a copy made with `CREATE DATABASE ... TEMPLATE`. The
fixture only drops databases it created: names are random
(`podex_test_[template_]<32 hex>`), an existing name is never adopted (creation
retries with a new name, a bounded number of times), and a failed create never
makes a name owned. A database is recorded as owned in the same step that
creates it, inside the `try` whose `finally` drops it, with SIGINT and SIGTERM
held (recorded, then raised again) until that step ends; the drop
(`DROP DATABASE ... WITH (FORCE)`) runs with them held too. While databases
exist, SIGTERM ends the session the way Ctrl-C does, and the default handler is
put back only if no one replaced ours meanwhile. Cleanup still cannot run after
SIGKILL, after a connection lost between the server creating a database and the
fixture seeing the reply, or when the fixture runs outside the main thread. The
database named in the URL is used only to create and drop databases, so the
fixture never reads or writes a table it did not create, and random names keep
parallel workers and concurrent runs apart. We chose migrated databases over
`Base.metadata.create_all` so the lane tests the schema production runs, and
databases over schemas because `CREATE EXTENSION vector` and the Alembic version
table are per database, which makes schema-level isolation leaky.

We do not sweep stale databases at session start: PostgreSQL records no reliable
creation time for a database, so a sweep could not prove a match was abandoned
rather than in use by a concurrent run. Leftovers are removed by hand with the
name-pattern sweep in `CONTRIBUTING.md`, on a disposable server, after listing
and checking the matches. We also do not try to detect whether a server is
disposable, because there is no reliable signal; the documentation and the
never-drop-what-you-did-not-create rule are the protection.

Races use `tests/postgres/concurrency.py`: the second transaction runs on its
own connection, and the first commits only after the server shows the second
waiting for the lock under test: waiting in the expected statement
(`pg_stat_activity`), blocked by the first transaction's backend
(`pg_blocking_pids`) and, for row locks, on a tuple lock of the expected table
(`pg_locks`). A wait at some later write does not count. No sleeps decide
ordering. Each race also runs with its production row lock removed and must then
fail.

Tests that need a whole pytest session launch an inner run on an isolated copy
of the scaffolding, with explicit node ids and `cwd`, and set
`PODEX_TESTS_INNER_RUN` for it. A run that sees that variable refuses to launch
another (at collection and at launch), and the lane script refuses to start, so
inner runs cannot nest.

CI runs `pytest tests -m postgres --require-postgres` in the
`🐘 Postgres Application Tests` job against the same digest-pinned pgvector
image as the migration replay, and the combined test gate includes its result.
The gate accepts a skipped test job only on a draft pull request. The lane does
not collect coverage; the 85% floor stays on the SQLite coverage job. We
deliberately do not run the whole SQLite suite on PostgreSQL.

## Consequences

- Feature work that depends on PostgreSQL semantics adds its regressions to
  `backend/tests/postgres/` with the marker; they run in CI and skip locally
  without a PostgreSQL URL.
- The lane must point at a disposable server, and its role must be a superuser:
  installing pgvector needs one on stock images. The CI image's `POSTGRES_USER`
  is one.
- Each test pays for one `CREATE DATABASE` from a template; each process pays
  for one migration replay.
- Code exercised only by PostgreSQL tests does not count toward coverage.
