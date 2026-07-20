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
uv run lintro fmt   # auto-fix formatting
uv run lintro chk   # verify, must be clean
```

Do not invoke the underlying tools (ruff, black, mypy, eslint, etc.) directly.

## Commits

- Use [Conventional Commits](https://www.conventionalcommits.org/):
  `type(scope): summary` (e.g. `feat(api): add media search endpoint`).
- Keep the subject in the imperative mood and under ~72 characters.
- **Sign your commits** (`git commit -S`); the `main` ruleset requires signed
  commits.

## Pull requests

- Branch from `main`; one focused change per PR.
- PR titles must follow Conventional Commits (validated in CI).
- All required checks must pass before merge.
- Address review feedback (human and automated) before requesting merge.

## Architecture Decision Records

Decisions with lasting architectural consequence get an
[ADR](docs/adr/README.md) **in the same PR that implements them**.
Grilling sessions that produce decisions also produce ADRs.

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
