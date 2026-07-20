<!-- markdownlint-disable MD041 -->
# 0001. Nested settings models with hard cutover

- Status: accepted
- Date: 2026-07-20
- Tags: config, pydantic-settings, deployment

## Context

`Settings` in `backend/src/podex/config.py` was a single flat
pydantic-settings class (~40 fields under the `PODEX_` prefix). Domain
groups (database, auth, billing, transcripts, rate limiting, …) shared one
namespace, so related knobs were hard to pass as a typed unit and easy to
mis-name in deployment templates.

A grilling session on the enrichment provider registry (#349) settled that
nested sub-models are the better long-term shape. Podex still has no
production users, so this is the cheapest moment for a **hard cutover**:
change the env grammar once, update every consumer in the same release,
and refuse aliases that would freeze the flat names forever.

## Decision

1. Enable `env_nested_delimiter="__"` on `Settings` while keeping
   `env_prefix="PODEX_"`. Nested fields use
   `PODEX_<DOMAIN>__<FIELD>` (for example `PODEX_DATABASE__URL`).
2. One pydantic `BaseModel` sub-model per domain, attached as a field on
   `Settings` (for example `settings.database: DatabaseSettings`).
   App-level basics (`app_name`, `environment`, `debug`, `cors_origins`,
   `public_web_url`, `api_v2_prefix`, …) stay top-level.
3. **No aliases.** Flat names for a migrated domain stop working in the
   same PR that nests that domain. `extra="ignore"` continues to drop
   unknown env keys rather than mapping them.
4. Migrate domains one PR at a time after this foundation; the database
   domain is the exemplar that proves the grammar end-to-end.

Deliberate non-choices: keeping `Field(validation_alias=…)` bridges for
old flat names; dual-read periods; optional-extra aliases in
podex-ops templates.

## Consequences

- Call sites can pass `settings.database` (and later peer sub-models) as
  one typed object; sub-models are reusable in tests via
  `DatabaseSettings(...)`.
- Every deployment template that sets a migrated domain must switch to
  the nested grammar **before** provisioning gates run. Sync
  lgtm-hq/podex-ops#15 (and any Railway/Neon env sheets) ahead of the
  next cloud cutover — this repo does not provision cloud resources.
- Sibling domain migrations (rate limit, stats cache, transcripts/R2,
  auth, billing, observability) follow the same hard-cutover rule.
- Operators who still export `PODEX_DATABASE_URL` will silently fall back
  to the SQLite default until they rename to `PODEX_DATABASE__URL`.
