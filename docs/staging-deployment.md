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
- `TRANSCRIPT_ARTIFACT_ENCRYPTION_KEY`
- `TRANSCRIPT_ARTIFACT_S3_BUCKET`
- `MEILI_MASTER_KEY`
- `API_KEY`
- `CORS_ORIGINS`
- `PUBLIC_API_URL`
- `PUBLIC_WEB_URL`
- `SMTP_HOST`
- `SMTP_FROM_EMAIL`

Set `SMTP_USERNAME` and `SMTP_PASSWORD` when required by the delivery provider.
Magic-link account sign-in is unavailable until SMTP delivery is configured.

`PUBLIC_API_URL` is baked into the Astro server build and should use the browser
reachable backend URL, including the `/api/v2` prefix used by the Astro client.
`PUBLIC_WEB_URL` is baked into discovery metadata and must be the public frontend
origin used for canonical links, structured data, and the sitemap.

Transcript raw-artifact payloads are encrypted by the backend before upload to a
private S3-compatible bucket. For AWS S3, set `TRANSCRIPT_ARTIFACT_S3_REGION_NAME`
and provide credentials through the host or workload credential provider chain.
For another S3-compatible provider, also set `TRANSCRIPT_ARTIFACT_S3_ENDPOINT_URL`
and, when required, the access-key variables in `.env.staging`. The bucket must
not allow public access.

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
