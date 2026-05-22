# Staging Deployment

Podex staging runs the real backend, frontend, Postgres, and Meilisearch services.
It does not seed mock data on startup.

## Prerequisites

- Docker Compose with access to a Docker daemon.
- A staging domain or host for the frontend.
- A backend origin that browsers can reach.
- Real ingestion credentials only when running provider-backed jobs.

## Configure

Create a staging environment file from the tracked template:

```bash
cp .env.staging.example .env.staging
```

Replace every `change-me` value in `.env.staging`. Keep `.env.staging` untracked.
The required values are:

- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `MEILI_MASTER_KEY`
- `API_KEY`
- `CORS_ORIGINS`
- `PUBLIC_API_URL`

`PUBLIC_API_URL` is baked into the Astro server build and should use the browser
reachable backend URL, including `/api/v1` until the frontend migrates to v2.

## Deploy

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml up -d --build
```

The backend container runs `alembic upgrade head` during startup when
`AUTO_MIGRATE=true`. It does not run mock seed data unless `SEED_MOCK_DATA=true`
is set explicitly.

## Verify

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml ps
curl -f http://localhost:8000/health
curl -f http://localhost:4321/
```

Expected backend health response:

```json
{"status":"healthy","service":"Podex","version":"0.1.0","db_connected":true}
```

## Load Real Podcast Data

Sync tracked podcast sources, then discover or scrape real JRE data as needed:

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml exec backend \
  python scripts/manage_podcasts.py sync --config ../podcasts.yaml

docker compose --env-file .env.staging -f docker-compose.staging.yml exec backend \
  python scripts/discover_episodes.py --podcast jre

docker compose --env-file .env.staging -f docker-compose.staging.yml exec backend \
  python scripts/scrape_podscripts.py --store-db --limit 5
```

Extraction and enrichment require their provider API keys to be configured in the
backend environment.

## Cleanup

```bash
docker compose --env-file .env.staging -f docker-compose.staging.yml down -v --rmi local
```
