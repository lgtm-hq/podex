# AGENTS.md

## Architecture Decision Records

Decisions with lasting architectural consequence require an ADR under
`docs/adr/`. Prefer the same PR that implements them; when the decision
precedes code, a docs-only PR is fine. Use `docs/adr/template.md`, update
the index in `docs/adr/README.md`, and treat accepted ADRs as immutable
(supersede instead of editing). Grilling sessions that settle design must
leave an ADR, not only issue comments. See `CONTRIBUTING.md` for the full
convention.

## Cursor Cloud specific instructions

Podex is a monorepo with two apps that together form the product:

- `backend/` — FastAPI + SQLAlchemy REST API (`/api/v2` + `/health`),
  managed with `uv`, SQLite by default.
- `frontend/` — Astro/React/Tailwind SSR discovery site (port 4321),
  managed with `bun`, which fetches podcasts from the backend.

Standard commands live in `CONTRIBUTING.md` and the manifests
(`backend/pyproject.toml`, `frontend/package.json`); prefer those. Key
ones: backend tests `uv run pytest`, backend lint `uv run lintro chk` /
`uv run lintro fmt`, frontend tests `bun run test:run`, frontend
type/build check `bun run check`.

Non-obvious caveats:

- **Backend deps: run `uv sync` (not `uv sync --extra dev`).**
  `CONTRIBUTING.md` says `--extra dev`, but `dev` is a
  `[dependency-groups]` entry, not an optional extra, so `--extra dev`
  fails. `uv sync` installs the dev group by default.
- **Run migrations before starting/using the API:**
  `cd backend && uv run alembic upgrade head`. `alembic.ini` has an
  empty `sqlalchemy.url`; `alembic/env.py` falls back to
  `PODEX_DATABASE_URL` (default `sqlite:///./podex.db`), so migrations
  and the app share the same DB file created in the `backend/` working
  dir. This is a one-shot setup step and is intentionally NOT in the
  automatic update script.
- **Run the servers from their app dirs:**
  - Backend:
    `cd backend && uv run uvicorn podex.main:app --reload --port 8000`
  - Frontend: `cd frontend && bun run dev`
    (serves on `http://localhost:4321`)
- **Frontend → backend wiring:** the frontend calls
  `http://localhost:8000/api/v2` by default (override with
  `PUBLIC_API_URL`); backend CORS already allows
  `http://localhost:4321`. Start the backend before the frontend for
  data to load.
- **The `/api/v2` surface is read-only (GET only).** There are no write
  endpoints yet; the LLM ingestion pipeline described in the README is
  not implemented. To get data into the catalog for local testing, seed
  rows directly via the ORM (e.g. `uv run python -c "..."` using
  `podex.database.SessionLocal` and `podex.models.Podcast`). An empty
  catalog renders "No podcasts yet." in the UI.
- `uv` is installed at `~/.local/bin/uv` and `bun` at
  `~/.bun/bin/bun` (added to `~/.bashrc` at setup time).
