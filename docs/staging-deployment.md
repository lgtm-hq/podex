# Staging deployment

This guide covers the **current** Podex stack on `main`: a read-only FastAPI
catalog API, an Astro SSR discovery frontend, and PostgreSQL. It does **not**
start transcript workers, Meilisearch projection, magic-link auth, billing, or
other pipeline services that are planned but not implemented yet.

## Prerequisites

- Docker Engine with Compose v2
- A copy of the staging env file (see below)

## 1. Configure environment

From the repository root:

```bash
cp .env.staging.example .env.staging
```

Edit `.env.staging` and replace every `change-me-*` placeholder. The root
`.env.staging.example` is the single source of truth for staging secrets and
service wiring. Compose loads it into containers via `env_file` (required —
the stack will not start without `.env.staging`).

| Variable | Read by | Purpose |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `db` | Postgres password |
| `PODEX_DATABASE_URL` | `api` (Alembic + runtime) | SQLAlchemy connection string |
| `PODEX_ENVIRONMENT` | `api` | Runtime environment label |
| `PODEX_CORS_ORIGINS` | `api` | Allowed browser origins (JSON list) |
| `API_PORT` / `FRONTEND_PORT` | compose | Host ports published for `api` / `frontend` |
| `PUBLIC_API_URL` | `frontend` (build arg) | Base URL for SSR API fetches |
| `TRANSCRIPT_*`, `MEILI_*`, `API_KEY`, `SMTP_*` | *(future)* | Reserved for pipeline/auth; unused on main |

`.env.staging` is git-ignored; never commit real secrets.

### Database credentials

Set both `POSTGRES_PASSWORD` and `PODEX_DATABASE_URL` in `.env.staging`. The
password in the URL must match `POSTGRES_PASSWORD`. If the password contains
URI-reserved characters (`@`, `:`, `/`, `?`, `#`, etc.), URL-encode only the
password segment of `PODEX_DATABASE_URL`:

```bash
python -c "from urllib.parse import quote; print(quote('your-password', safe=''))"
```

Example:

```bash
POSTGRES_PASSWORD='p@ss:word'
PODEX_DATABASE_URL=postgresql://podex:p%40ss%3Aword@db:5432/podex
```

### Host ports and CORS

When overriding `FRONTEND_PORT`, update `PODEX_CORS_ORIGINS` so it includes the
browser origin you will use (scheme, host, and port), for example:

```bash
FRONTEND_PORT=3000
PODEX_CORS_ORIGINS=["http://localhost:3000"]
```

The same applies when exposing the stack on a non-localhost hostname.

## 2. Start the stack

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging up --build -d
```

`--env-file` supplies host-port overrides for compose interpolation; container
secrets are loaded from the same file via `env_file`.

Services:

| Service | Port | Description |
| --- | --- | --- |
| `db` | *(internal)* | PostgreSQL 16 |
| `api` | `${API_PORT:-8000}` | FastAPI (`uvicorn`) |
| `frontend` | `${FRONTEND_PORT:-4321}` | Astro SSR (`node` standalone) |

## 3. Run database migrations

Migrations are **not** run automatically. After the database is healthy:

```bash
docker compose -f docker-compose.staging.yml --env-file .env.staging \
  run --rm api alembic upgrade head
```

Alembic reads `PODEX_DATABASE_URL` via `backend/src/podex/config.py`.

## 4. Verify

Use the same port variables from `.env.staging` (defaults shown):

```bash
API_PORT="${API_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-4321}"

curl "http://localhost:${API_PORT}/health"
curl "http://localhost:${API_PORT}/api/v2/podcasts"
# open http://localhost:${FRONTEND_PORT}
```

An empty catalog shows **No podcasts yet.** Seed data via the ORM if you need
sample rows (there are no write API endpoints on main).

## Validate compose config

Lint the compose file with a populated `.env.staging` (compose requires the
file to exist):

```bash
cp .env.staging.example .env.staging
# replace change-me-* placeholders, then:
docker compose -f docker-compose.staging.yml --env-file .env.staging config
```

No secrets are embedded in `docker-compose.staging.yml`; all credentials live
only in `.env.staging`.

## Future services

`docker-compose.staging.yml` includes commented stubs for Redis (shared rate
limits, #107), Meilisearch, and worker/scheduler processes. Uncomment and wire
them only after the corresponding application code lands on `main`.
