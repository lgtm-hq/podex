# Contributing to Podex

Thanks for your interest in contributing. This guide covers the workflow and
standards for changes to Podex.

## Development setup

Backend (Python, managed with [`uv`](https://docs.astral.sh/uv/)):

```bash
cd backend
uv sync --extra dev
uv run pytest
```

Frontend (Astro/React, managed with [`bun`](https://bun.sh/)):

```bash
cd frontend
bun install
bun run test
```

CI shell scripts (`.github/scripts/`) have [BATS](https://github.com/bats-core/bats-core)
tests under `tests/bats/`:

```bash
bats --recursive tests/bats
```

The frontend API types (`src/lib/types.gen.ts`) are generated from the backend
OpenAPI schema (`frontend/openapi.json`). After changing an API route or schema,
regenerate both and commit the result (`bun run check` fails on drift):

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
- A few tests repeat external request or relationship fields to exercise separate
  contracts. Shared setup, payloads, and algorithms belong in reusable helpers.
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
- All required checks must pass before merge. `test-suite / 🧪 Test Suite`
  is the combined test gate: it fails if the Python matrix, coverage,
  Postgres migration replay or shell tests fail.
- Address review feedback (human and automated) before requesting merge.

## Architecture Decision Records

Decisions with lasting architectural consequence get an
[ADR](docs/adr/README.md). Prefer the **same PR that implements them**;
when the decision precedes code, a docs-only PR is fine. Grilling
sessions that produce decisions also produce ADRs.

- Use `docs/adr/template.md` and add a row to `docs/adr/README.md`.
- Accepted ADRs are immutable; supersede with a new ADR rather than
  editing the Decision.
- Backfilled historical decisions use status `accepted (backfilled)`.

Skip an ADR for routine refactors, bug fixes, or choices already fully
covered by an accepted ADR.

## Reporting issues

Open a [GitHub issue](https://github.com/lgtm-hq/podex/issues) using a
`type(scope): summary` title. Include reproduction steps for bugs and the
motivation for feature requests.
