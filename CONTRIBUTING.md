# Contributing to Podex

Thanks for your interest in contributing. This guide covers the workflow and
standards for changes to Podex.

## Development setup

Backend (Python, managed with [`uv`](https://docs.astral.sh/uv/)):

The backend requires Python **3.11.5 or newer**. Python 3.11.0–3.11.4 must be
upgraded before installing this release: those patches select a vulnerable Soup
Sieve dependency. The CI matrix continues to cover Python 3.11–3.15 using
current patch releases; the Docker image uses Python 3.15.

```bash
cd backend
uv sync                       # installs the dev dependency group by default
uv run alembic upgrade head   # creates or migrates the database
uv run pytest
```

`uv run alembic upgrade head` migrates the database named by
`PODEX_DATABASE__URL`, by default `podex.db` in the working directory. Run the
API and the scheduler from `backend/` as two processes:

```bash
uv run uvicorn podex.main:app --reload --port 8000 --no-access-log
uv run python -m podex.scheduler_runner
```

The scheduler runs episode discovery, digest delivery and retention sweeps.
Neither process needs an external service to start; see
[docs/configuration.md](docs/configuration.md) for the settings that enable each
provider and what happens without them.

Frontend (Astro/React, managed with [`bun`](https://bun.sh/)):

```bash
cd frontend
bun install
bun run test:run   # one-shot Vitest run; `bun run test` starts watch mode
bun run check      # astro check, then generated API type drift
bun run dev        # serves http://localhost:4321
```

The frontend calls `http://localhost:8000/api/v2` unless `PUBLIC_API_URL` is
set.

Tests that need real PostgreSQL carry `@pytest.mark.postgres` and live under
`backend/tests/postgres/`, where collection rejects any test without the marker
and the SQLite fixtures (`db_session`, `client`, `seeded_graph`) fail. Their
`postgres` fixture gives each test a private database migrated to head. A plain
`uv run pytest` skips them with the reason; `--require-postgres` (what CI uses)
fails them instead when `PODEX_DATABASE__URL` is not a PostgreSQL URL.

**Use a disposable server only.** The fixture creates and force-drops
(`DROP DATABASE ... WITH (FORCE)`) whole databases on the server
`PODEX_DATABASE__URL` points at. It drops only databases it created itself and
never reads or writes tables in the URL's database, but do not point it at a
server holding data you care about. The role must be a **superuser**: each run
installs the pgvector extension in a new database, and stock pgvector images
allow that only to superusers (`CREATEDB` alone fails with "Must be superuser to
create this extension"). The image CI uses, started as below, provides both;
`POSTGRES_USER` is a superuser there:

```bash
docker run -d --rm --name podex-pg -p 5432:5432 -e POSTGRES_USER=podex \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=podex \
  pgvector/pgvector:pg16@sha256:484e838076e04939d27d3e176e91c86eea96236126023be8d3059761122e579f
cd backend
PODEX_DATABASE__URL=postgresql+psycopg://podex@localhost:5432/podex \
  uv run pytest tests -m postgres --require-postgres
docker rm -f podex-pg
```

Each database is recorded as owned in the same step that creates it, with SIGINT
and SIGTERM held until that step ends, and its drop also runs with them held. So
a failing test, Ctrl-C or SIGTERM drops it. Cleanup cannot run when:

- the run is killed with SIGKILL (or the machine stops);
- the connection is lost after the server created a database but before the
  fixture saw the reply;
- the fixture is driven from a thread other than the main one, where Python
  cannot install signal handlers.

Leftovers are named `podex_test_<32 hex>` or `podex_test_template_<32 hex>`.
Removing them is a sweep by name pattern, so do it only on a disposable server
and only when no test run is using it: a concurrent run's databases match the
same pattern. List the matches first:

```bash
docker exec -i podex-pg psql -U podex -d podex <<'SQL'
SELECT datname
FROM pg_database
WHERE datname ~ '^podex_test_(template_)?[0-9a-f]{32}$'
ORDER BY datname;
SQL
```

Check that every listed name is a leftover you expect to remove, then drop them:

```bash
docker exec -i podex-pg psql -U podex -d podex <<'SQL'
SELECT format('DROP DATABASE %I WITH (FORCE)', datname)
FROM pg_database
WHERE datname ~ '^podex_test_(template_)?[0-9a-f]{32}$'
\gexec
SQL
```

CI shell scripts (`.github/scripts/`) have
[BATS](https://github.com/bats-core/bats-core) tests under `tests/bats/`:

```bash
bats --recursive tests/bats
```

The frontend API types (`src/lib/types.gen.ts`) are generated from the backend
OpenAPI schema (`frontend/openapi.json`). After changing an API route or schema,
regenerate both and commit the result. `backend/tests/test_openapi_schema.py`
fails when `openapi.json` differs from the backend's schema, and `bun run check`
fails when `types.gen.ts` differs from `openapi.json`:

```bash
cd backend && uv run python -m podex.openapi ../frontend/openapi.json
cd frontend && bun run generate:api
```

## Linting and formatting

Podex uses [`lintro`](https://github.com/lgtm-hq/py-lintro) as the single entry
point for linting and formatting. Run it before every commit:

```bash
cd backend
uv run lintro fmt   # auto-fix formatting
uv run lintro chk   # verify, must be clean
```

Do not invoke the underlying tools (ruff, black, mypy, eslint, etc.) directly.

Pylint is pinned in the backend development group. The quality container starts
its tools outside the application's virtual environment, so the Pylint init hook
in `backend/scripts/ci/quality/pylint_environment.py` synchronizes the frozen
backend dependencies and restarts Pylint with that environment's interpreter.
This keeps imports and Pylint/Astroid framework inference consistent with local
checks; CI needs package-registry access on a cold dependency cache.

Keep diagnostic exceptions narrow and explain them next to the affected code.
The baseline has these intentional boundaries:

- Declarative ORM/Pydantic fields, flat data records, public command signatures,
  and single-operation adapters retain their framework or caller contracts.
  SQLAlchemy mapping annotations use forward references for reliable inference.
- Alembic's runtime proxies and numbered migration names follow its conventions.
  Historical migration DDL stays self-contained rather than importing mutable
  application helpers; only duplicated DDL blocks are exempt from similarity
  checks, while all other checks remain enabled.
- API/service DTO declarations and explicit provider result mappings remain
  independent at their validation boundaries. Similarity exceptions cover only
  those declarations or mappings, not provider algorithms or request handling.
- Independent provider/task failures are caught at orchestration boundaries so
  one failure does not stop the remaining work. Direct unit tests of private
  boundaries use local `protected-access` exceptions.
- A few tests repeat external request or relationship fields to exercise
  separate contracts. Shared setup, payloads, and algorithms belong in reusable
  helpers.
- `.lintro/` contains generated reports and is excluded from source discovery.
  TruffleHog alone omits `uv.lock` because a generated PyPI Sentry SDK integrity
  digest resembles a credential; OSV and Gitleaks still scan the lockfile.

No Pylint diagnostic is disabled globally. Add new exceptions only after fixing
the underlying cause or establishing why the explicit boundary must remain.

## Commits

- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary` (e.g. `feat(api): add media search endpoint`).
- Keep the subject in the imperative mood and under ~72 characters.
- **Sign your commits** (`git commit -S`); the `main` ruleset requires signed
  commits.

## Pull requests

- Branch from `main`; one focused change per PR.
- PR titles must follow Conventional Commits (validated in CI).
- All required checks must pass before merge. `test-suite / 🧪 Test Suite` is
  the combined test gate: it fails if the Python matrix, coverage, Postgres
  migration replay, Postgres application tests or shell tests fail.
- Address review feedback (human and automated) before requesting merge.

## Architecture Decision Records

Decisions with lasting architectural consequence get an
[ADR](docs/adr/README.md). Prefer the **same PR that implements them**; when the
decision precedes code, a docs-only PR is fine. Grilling sessions that produce
decisions also produce ADRs.

- Use `docs/adr/template.md` and add a row to `docs/adr/README.md`.
- Accepted ADRs are immutable; supersede with a new ADR rather than editing the
  Decision.
- Backfilled historical decisions use status `accepted (backfilled)`.

Skip an ADR for routine refactors, bug fixes, or choices already fully covered
by an accepted ADR.

## Reporting issues

Open a [GitHub issue](https://github.com/lgtm-hq/podex/issues) using a
`type(scope): summary` title. Include reproduction steps for bugs and the
motivation for feature requests.
