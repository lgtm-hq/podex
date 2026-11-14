# AGENTS.md

## Architecture Decision Records

Decisions with lasting architectural consequence require an ADR under
`docs/adr/`. Prefer the same PR that implements them; when the decision precedes
code, a docs-only PR is fine. Use `docs/adr/template.md`, update the index in
`docs/adr/README.md`, and treat accepted ADRs as immutable (supersede instead of
editing). Grilling sessions that settle design must leave an ADR, not only issue
comments. See `CONTRIBUTING.md` for the full convention.

## Cursor Cloud specific instructions

Podex is a monorepo with two apps that together form the product:

- `backend/` — FastAPI + SQLAlchemy REST API (`/api/v2` + `/health`), managed
  with `uv`, SQLite by default.
- `frontend/` — Astro/React/Tailwind SSR discovery site (port 4321), managed
  with `bun`, which fetches podcasts from the backend.

Standard commands live in `CONTRIBUTING.md` and the manifests
(`backend/pyproject.toml`, `frontend/package.json`); prefer those. Key ones, run
inside the app directory: backend tests `uv run pytest`, backend lint
`uv run lintro chk` / `uv run lintro fmt`, frontend tests `bun run test:run`,
frontend type/build check `bun run check`.

Non-obvious caveats. Commands below run from the repository root; `( ... )`
keeps each `cd` inside a subshell so the shell stays at the root.

- **Run migrations before starting/using the API:**
  `(cd backend && uv run alembic upgrade head)`. `alembic.ini` has an empty
  `sqlalchemy.url`; `alembic/env.py` falls back to `PODEX_DATABASE__URL`
  (default `sqlite:///./podex.db`), so migrations and the app share the same DB
  file created in the `backend/` working dir. This is a one-shot setup step and
  is intentionally NOT in the automatic update script.
- **Run the servers from their app dirs, each in its own terminal:**
  - Backend:
    `(cd backend && uv run uvicorn podex.main:app --reload --port 8000 --no-access-log)`
    (matches the container: Uvicorn's access log prints query strings, so
    request lines come from the app's path-only `podex.access` logger)
  - Frontend: `(cd frontend && bun run dev)` (serves on `http://localhost:4321`)
- **Frontend → backend wiring:** the frontend calls
  `http://localhost:8000/api/v2` by default (override with `PUBLIC_API_URL`);
  backend CORS already allows `http://localhost:4321`. Start the backend before
  the frontend for data to load.
- **The `/api/v2` surface has four kinds of route.** Settings for each are in
  [`docs/configuration.md`](docs/configuration.md).
  - Public, no credentials: `GET` on `/status`, `/podcasts`, `/episodes`,
    `/media` and `/stats`; `POST /takedowns`; and the sign-in routes under
    `/auth` (magic link, WorkOS login and callback, logout).
  - Account, authenticated by the session cookie that sign-in sets: `/me` and
    everything under it (saves, follows, alerts, digests, preferences,
    subscription checkout, export, account deletion).
  - Ops, authenticated by an `X-Ops-Key` header equal to `PODEX_OPS_API_KEY`
    (503 while the key is unset): `/ops/*`, including podcast create, update and
    archive, retention apply and purge, and takedown decisions. The root
    Prometheus `/metrics` uses the same key.
  - Webhook: `POST /billing/webhooks/paddle`, verified with
    `PODEX_BILLING__PADDLE_WEBHOOK_SECRET`.
- **Getting data in locally:** start the backend with an ops key in one
  terminal, then create podcasts through the ops API from another:

  ```bash
  (cd backend && PODEX_OPS_API_KEY=local-dev-key \
    uv run uvicorn podex.main:app --reload --port 8000 --no-access-log)
  ```

  ```bash
  curl -X POST http://localhost:8000/api/v2/ops/podcasts \
    -H 'X-Ops-Key: local-dev-key' -H 'Content-Type: application/json' \
    -d '{"name": "Example", "slug": "example", "status": "active",
         "sources": {"rss_url": "https://example.com/feed.xml"}}'
  ```

  New podcasts default to `watchlist`; only `active` ones get scheduled
  discovery. Seeding rows directly through the ORM
  (`podex.database.SessionLocal` and the models in `podex.models`) also works.
  An empty catalog renders "No podcasts yet." in the UI.

- **Scheduler:** `(cd backend && uv run python -m podex.scheduler_runner)`, in
  its own terminal, runs a separate loop with no HTTP listener. Each tick it
  schedules and runs episode discovery for active podcasts (podscripts.co, RSS,
  and Spotify when `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` are set),
  digest delivery (needs SMTP) and retention sweeps. In containers it is the
  same image with the command overridden to `python -m podex.scheduler_runner`.
- **Ingestion pipeline is partial.** Discovery runs as above. Enrichment, LLM
  transcript cleanup, LLM mention extraction and extraction review exist as
  services but nothing in the API or scheduler calls them, and no transcript
  acquisition provider exists, so episodes do not get transcripts or mentions
  automatically. See [#59](https://github.com/lgtm-hq/podex/issues/59).
- `uv` is installed at `~/.local/bin/uv` and `bun` at `~/.bun/bin/bun` (added to
  `~/.bashrc` at setup time).
