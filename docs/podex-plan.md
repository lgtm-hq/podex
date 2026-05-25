# Podex End-State Product Plan

## 1. Summary

Podex is a curated multi-podcast product that turns podcast conversations into a structured, trustworthy discovery graph for media, people, places, studies, and related entities. The product is designed to help users answer not just what was mentioned, but also where it was mentioned, when it was mentioned, who mentioned it, how often it appears, and how confident the system is in the underlying data.

The end-state product has a public + ops shape. The public side is a fast discovery experience for browsing, searching, filtering, and understanding podcast-driven recommendations. The ops side is a private operational platform for source onboarding, ingestion monitoring, review workflows, metadata correction, search tuning, and auditability.

Architecturally, Podex is a modular monolith with decoupled boundaries. The system keeps business domains isolated behind stable interfaces so that refactoring remains localized and eventual service extraction remains possible without rewriting the product. The design favors explicit contracts, projection-driven read models, and pipeline stages with clear responsibilities over tightly coupled CRUD layers.

Trust is a product feature, not a backend detail. Podex emphasizes provenance, confidence scoring, hybrid review, canonical entity management, and operator visibility into every stage of the pipeline. Search, stats, and discovery surfaces are powered by published catalog records and search projections, while uncertain candidates stay isolated until policy or operator review makes them safe to publish.

## Progress Snapshot

### Current Status

- **Overall status:** Phase 1, Phase 2, Phase 2.5, and Phase 3 are complete in the implementation plan; Podex now has dashboard, podcast management, pipeline inspection, review tooling, canonical media management, search operations, audited transcript retention, and coordinated takedown/creator opt-out workflows built on the v2 ops contracts.
- **Current focus:** All planned implementation phases are complete; paid activation and any open-core relicensing remain blocked on recorded external legal/owner approval.

### Current Product Reality

- **Backend:** `/api/v2` public discovery, ops/admin contracts, review queue + audit log flows, replay-safe extraction provenance, search projection repair hooks, encrypted local/S3-compatible transcript artifact adapters, artifact-backed cleanup/extraction reads, passwordless account sessions, saves, follows, alert-rule evaluation, SMTP-backed digest delivery, persisted notification preferences, and launch-gated subscription/quota enforcement are all landed.
- **Frontend:** The public Astro site ships source, media, episode, stats, pricing, public Terms and Privacy pages, account sign-in/verification, dedicated saved/following/alerts/settings destinations with digest and membership controls, media save and source follow actions, and ops routes for dashboard, podcast, pipeline, review, audited canonical media editing, search projection/tuning, retention sampling, and transcript lifecycle preview/purge/re-acquisition workflows backed by the v2 API client.
- **Not yet shipped:** Recorded external counsel review/sign-off remains a paid-launch blocker; any change from the existing MIT license remains subject to owner and legal approval.

### Issue Tracking Overview

Retroactive parents grouping already-landed work: [#31](https://github.com/lgtm-hq/podex/issues/31) v2 API foundation, [#32](https://github.com/lgtm-hq/podex/issues/32) shared services, [#33](https://github.com/lgtm-hq/podex/issues/33) v2 public discovery, [#34](https://github.com/lgtm-hq/podex/issues/34) v2 ops + admin, [#35](https://github.com/lgtm-hq/podex/issues/35) candidate-backed extraction, [#36](https://github.com/lgtm-hq/podex/issues/36) backend tests.

Roadmap parents: [#58](https://github.com/lgtm-hq/podex/issues/58) foundational, [#59](https://github.com/lgtm-hq/podex/issues/59) Phase 2, [#60](https://github.com/lgtm-hq/podex/issues/60) Phase 2.5, [#61](https://github.com/lgtm-hq/podex/issues/61) Phase 3, [#62](https://github.com/lgtm-hq/podex/issues/62) Phase 4, [#63](https://github.com/lgtm-hq/podex/issues/63) Phase 5, [#64](https://github.com/lgtm-hq/podex/issues/64) Phase 6. The Phase 2 and Phase 2.5 parent issues may remain open for issue-tracker cleanup even though their implementation checklists are complete here.

### Completed So Far

- [x] Added `/api/v2` routing and application wiring. (#37)
- [x] Added a dedicated v2 public API foundation. (#37)
- [x] Added a dedicated v2 ops API foundation. (#37)
- [x] Added a first ops dashboard endpoint. (#38)
- [x] Added shared catalog/status query services so v1 and v2 can reuse backend logic. (#39)
- [x] Added shared media query services so v1 and v2 can reuse media/detail logic. (#39)
- [x] Added shared public search services so v1 and v2 can reuse discovery logic. (#39)
- [x] Added shared public episode query services so v1 and v2 can reuse episode/detail logic. (#39)
- [x] Refactored existing v1 podcast, status, search, and episode routes to use shared services. (#43)
- [x] Added grouped `/api/v2/search` with opaque IDs and future-facing public resource URLs. (#44)
- [x] Added typed `/api/v2/episodes` list/detail/mentions endpoints with opaque IDs and nested media references. (#45)
- [x] Added typed `/api/v2/trends` discovery coverage for overview, by-type, and top-mentioned views. (#46)
- [x] Added a dedicated `/api/v2/ops/pipelines` contract for recent runs and transcription-job activity. (#48)
- [x] Added a dedicated `/api/v2/ops/pipelines/run` contract for operator-triggered pipeline runs. (#48)
- [x] Added a dedicated `/api/v2/ops/search` contract for search projection health and per-index stats. (#49)
- [x] Added typed `/api/v2/ops/podcasts` catalog management coverage for filtered inventory plus create/update/archive flows. (#50)
- [x] Added a dedicated `/api/v2/ops/episodes/{id}/rerun` contract for replaying selected processing jobs. (#51)
- [x] Added a dedicated `/api/v2/ops/media/{id}/merge` contract for canonicalizing duplicate media records. (#51)
- [x] Added a dedicated `/api/v2/ops/review-queue` contract backed by review-state models and shared review services. (#52)
- [x] Added a dedicated `/api/v2/ops/audit-log` contract backed by audit-state models and real privileged-action logging. (#53)
- [x] Added typed `/api/v2/admin/settings` coverage for runtime configuration snapshots and partial updates. (#53)
- [x] Added shared pipeline query services so ops surfaces do not depend on raw ORM access patterns. (#40)
- [x] Added shared pipeline mutation services so ops run and rerun surfaces do not depend on raw ORM writes. (#40)
- [x] Added shared search projection query services so ops surfaces do not depend on direct Meilisearch client access. (#41)
- [x] Added shared review queue services so review surfaces can publish, reject, and merge candidates without faking workflow state. (#42)
- [x] Added shared audit log services so ops and admin mutations land in a unified audit stream. (#42)
- [x] Routed transcript extraction output into replay-safe mention candidates and operator review items instead of directly publishing new mentions. (#54)
- [x] Added a typed review reclassification mutation so pending candidates can be corrected before approval while preserving provenance and audit history. (#52)
- [x] Added backend tests covering the first v2 discovery and ops activity slices. (#57)

### Recently Completed

- [x] Expand the v2 public surface with media-focused endpoints. (#47)
- [x] Expand the v2 public surface with search-focused endpoints. (#44)
- [x] Continue formalizing background job boundaries and projection-oriented contracts with a typed ops pipeline activity endpoint. (#48)
- [x] Continue broadening `/api/v2` discovery coverage beyond the first catalog and search surfaces with typed episode list/detail/mentions endpoints. (#45)
- [x] Continue separating projection and background-job concerns behind dedicated shared services with a typed ops search projection endpoint. (#49)
- [x] Continue broadening `/api/v2` discovery coverage into richer discovery surfaces such as trends, collections, or related-entity views. (#46)
- [x] Begin Phase 2 extraction workflow hardening by persisting replay-safe mention candidates and review queue items from transcript extraction. (#54)

### Current Phase

- [x] Phase 3: Ops Console (#61) — complete.

### Later Phases

- [ ] Phase 4: Account Product (#62) — product implementation complete; external legal review gate remains open before paid activation.
- [x] Phase 5: Public Product Polish (#63) - complete.
- [x] Phase 6: Stabilization (#64) - complete.

## 2. Product Definition

### Core Goal

Build the best structured index of high-signal references mentioned across a curated multi-podcast catalog, with clear provenance, reliable metadata, and a product experience that supports both public discovery and internal operations.

### Target Personas

- **Anonymous visitors** who want to search and browse podcast-driven recommendations.
- **Signed-in users** who want saves, follows, alerts, and digest-style personalization.
- **Operators** who manage podcast sources, monitor ingestion health, review extracted candidates, fix metadata, and control publication quality.
- **Admins** who manage credentials, roles, thresholds, auditing, and system settings.

### In Scope

- Curated multi-podcast catalog management.
- Public discovery experience for media, episodes, podcasts, and trends.
- Structured transcript-based mention tracking with provenance.
- Canonical media/entity management with aliases and external references.
- Optional public accounts for saves, follows, alerts, and digests.
- Private ops/admin tooling for onboarding, review, monitoring, reruns, and audits.
- Search and indexing as dedicated read paths over published content.
- Quality controls that combine automation with operator review.

### Out of Scope

- Open self-serve podcast ingestion for the public.
- Community submissions as a primary ingestion path.
- Social features such as comments, ratings, and public profiles.
- Multi-tenant enterprise workspaces.
- Real-time livestream indexing.
- Mobile-native applications in the initial end-state scope.

### Success Criteria

- Users can reliably discover references and trace them back to source episodes, timestamps, and transcript context.
- Operators can manage podcasts and content quality without direct database edits or YAML-only workflows.
- Ambiguous extraction output never appears publicly without policy-driven handling.
- Search remains fast and relevant as the catalog grows.
- Architectural changes remain localized because domain boundaries and interfaces are decoupled.

## 3. Product Surface

### Public Experience

- Search-first home page with featured trends, notable mentions, and curated discovery paths.
- Unified search across media, episodes, podcasts, people, places, studies, and editorial collections.
- Media detail pages with canonical metadata, mention timelines, related entities, provenance, and external references.
- Episode detail pages with transcript snippets, mention occurrences, timestamp jumps, and related catalog links.
- Source and podcast detail pages with coverage, recent indexing activity, notable mentions, and catalog depth.
- Stats and trends pages showing top mentions, distribution by type, trend lines, and source-level insights.
- Editorial collection pages such as most mentioned books, recurring studies, or frequently referenced documentaries.

### Account Experience

- Passwordless sign-in and session management.
- Saved media, episodes, podcasts, and collections.
- Follow functionality for podcasts, media items, topics, and entities.
- Alert rules for new mentions, new episodes, or matching categories.
- Digest preferences for notification cadence and content scope.
- Account settings for email preferences, privacy controls, and basic profile metadata.

### Ops and Admin Experience

- Operational dashboard for ingestion health, search lag, queue depth, review backlog, and provider status.
- Podcast catalog management for adding, pausing, editing, and archiving tracked sources.
- Discovery and pipeline run inspection with stage-level status, timing, and error visibility.
- Review queue for approving, rejecting, merging, splitting, or reclassifying extracted candidates.
- Canonical media management with duplicate resolution, alias editing, metadata correction, and merge previews.
- Search tooling for reindexing, synonym tuning, ranking inspection, and projection diagnostics.
- Audit log for privileged actions and content-affecting decisions.
- Admin settings for credentials, thresholds, roles, templates, and platform-wide configuration.

## 4. Architecture

### Modular Monolith Rationale

Podex is implemented as a modular monolith because the product needs clear domain boundaries, shared transactional guarantees, and rapid iteration without the overhead of early service fragmentation. This approach supports decoupled implementations and keeps future refactoring tractable by enforcing interface-level separation now.

### Deployables

- **Web frontend** for the public site and authenticated account experience.
- **API backend** for public, account, ops, and admin interfaces.
- **Worker process** for discovery, transcript acquisition, extraction, enrichment, indexing, and notifications.
- **Scheduler process** for recurring ingestion, reindexing, and digest generation.
- **Postgres** as the source of truth.
- **Meilisearch** as the search projection engine.
- **Redis-backed async processing** for queues, coordination, and short-lived caches.
- **Object storage** for transcript artifacts, exports, and large pipeline byproducts.

### Domain and Module Boundaries

- **Catalog** for podcasts, sources, episodes, and source configuration.
- **Transcripts** for acquisition, cleanup, storage, snippet generation, and provenance.
- **Extraction** for candidate generation, confidence scoring, and candidate normalization.
- **Review** for queueing, assignment, approval, rejection, and override workflows.
- **Media** for canonical entities, aliases, external references, metadata enrichment, and relationships.
- **Search** for indexing, ranking, query translation, and analytics.
- **Accounts** for users, sessions, saves, follows, alerts, and preferences.
- **Notifications** for digests, alert delivery, templates, and delivery history.
- **Ops** for dashboards, audit logs, reruns, health checks, and administrative controls.
- **Shared contracts** for IDs, enums, error types, events, and pagination primitives.

### Internal Layering

Each domain is organized into:

- **Domain layer** for business rules and policies.
- **Application layer** for use cases and orchestration.
- **Ports** for repositories and external provider contracts.
- **Adapters** for SQLAlchemy, Meilisearch, Redis, object storage, transcript providers, and LLM providers.
- **API layer** for request and response DTOs.

### Decoupling Rules

- HTTP handlers call application services, not persistence logic directly.
- Application services depend on ports, not concrete adapters.
- ORM models are persistence concerns, not public contracts.
- Search indexes are read-model projections, not authoritative data stores.
- Candidate-state objects remain separate from published catalog objects.
- Background side effects such as indexing and notifications are dispatched from explicit application flows and replayable job boundaries.
- Frontend code consumes typed API contracts rather than duplicating backend schema logic by hand.

### Source of Truth vs Projections

Postgres stores authoritative catalog, pipeline, and account data. Meilisearch stores a search projection optimized for discovery queries. Search relevance, snippets, and ranking are projection behavior layered over published records; they do not define catalog truth.

## 5. Data Model Direction

### Core Entities to Retain

- `Podcast`
- `Episode`
- `Transcript`
- `TranscriptionJob`
- `IngestionRun`
- `Media`
- `Mention`

### New Entities to Add

- `PodcastSource`
- `PipelineRun`
- `PipelineStepRun`
- `MentionCandidate`
- `ReviewItem`
- `MediaAlias`
- `MediaExternalRef`
- `MediaRelation`
- `VerificationRecord`
- `User`
- `UserSession`
- `SavedItem`
- `Follow`
- `AlertRule`
- `NotificationDelivery`
- `AuditLog`
- `SearchAnalyticsEvent`
- `EditorialCollection`
- `TranscriptRetentionPolicy`
- `TranscriptDigest`
- `SemanticChunk`
- `GraphTriple`
- `TakedownRequest`

### Publication Lifecycle

Podex uses an explicit lifecycle for extracted content:

1. **Candidate** for newly extracted and untrusted output.
2. **Published** for records that are safe to expose publicly.
3. **Retired or merged** for superseded, hidden, or consolidated records that still need historical traceability.

### Transcript Retention Lifecycle

Raw transcripts follow a separate retention lifecycle alongside the publication lifecycle, because raw source data carries copyright, privacy, and cost considerations that do not apply to extracted derivatives:

1. **Hot** for recently acquired transcripts under active extraction, review, or rerun.
2. **Warm** for transcripts whose derivatives are stable but where future reruns remain likely.
3. **Cold** for transcripts retained for audit, sampling, or occasional replay only.
4. **Purged** for transcripts whose raw payload has been deleted while a `TranscriptDigest` proof-of-processing record is preserved.

Retention transitions are policy driven rather than ad hoc, so purge decisions are explainable, reversible through re-acquisition where possible, and auditable.

### Transcript Storage Direction

- Keep transcript metadata and searchable snippet records in Postgres.
- Store large raw transcript artifacts and bulky derived payloads in object storage with encryption and ops-only access.
- Track provider, cleanup lineage, extraction provenance, and timestamps for every transcript asset.
- Apply `TranscriptRetentionPolicy` configured per `PodcastSource` to decide when raw transcripts move between hot, warm, cold, and purged states.
- Gate purge on a combination of minimum extraction confidence, stability across multiple extraction model generations, absence of open review items, and an expired cooling period.
- Always retain a stratified statistical sample of raw transcripts (roughly five to ten percent, stratified by podcast, topic, and confidence) as a permanent calibration corpus for future extraction models.
- Preserve a `TranscriptDigest` hash, the set of extraction model versions applied, and a summary of extracted outputs for every purged transcript so that processing history remains provable without retaining the raw text.
- Treat re-acquisition from upstream providers as an explicit supported path when a purged transcript needs to be reprocessed by an improved extraction model.

### Retention Sampling

The stratified retention sample is the permanent calibration corpus that makes future extraction-model upgrades possible even after raw transcripts are purged. The sample is governed by explicit dimensions rather than ad hoc selection:

- **Source dimension:** each active `PodcastSource` contributes proportionally to its episode volume so that no source is over- or under-represented.
- **Topic dimension:** samples span the canonical media types the product supports (for example books, films, studies, places, people) so that model evaluation covers the full extraction surface.
- **Confidence band dimension:** samples span low, medium, and high candidate-confidence buckets so that model comparisons can distinguish agreement on easy cases from agreement on hard ones.
- **Age bucket dimension:** samples span recent, mid-life, and older transcripts so that extraction-model drift over time is observable.
- **Target size:** roughly five to ten percent of eligible transcripts, adjusted per source to respect the dimensions above.

Sampled transcripts are flagged as retention-exempt, are never purged, and are excluded from cold-storage migration beyond the minimum needed for cost control. The sampling algorithm and its parameters are versioned so that later changes to sampling policy are auditable and reversible.

### Derivative Data Layer

Podex treats extracted derivatives as the durable product surface and raw transcripts as a purgeable intermediate. Derivatives are generated from transcripts, persisted independently, and designed to support public discovery, retrieval, and ranking without re-reading the raw source.

- **Structured mentions** remain the core truth layer via `Mention`, `MentionCandidate`, and transcript span/context metadata.
- **Semantic chunks** capture transcript windows with embeddings, short context snippets, and provenance through `SemanticChunk`, stored in Postgres with pgvector rather than new infrastructure.
- **Graph triples** capture entity-to-entity relationships across the catalog through `GraphTriple`, backed by `Media`, `MediaRelation`, and `MediaExternalRef`, with timestamped provenance to source episodes.
- **Episode and media summaries** provide cached, read-optimized narrative layers for discovery surfaces and are regenerated only when upstream data materially changes.

Derivative generation is treated as a first-class pipeline stage with the same idempotency, replayability, and provenance guarantees as extraction and indexing.

### Source Catalog Management Direction

Podcast and source configuration evolves from YAML-based initial import toward database-backed management. YAML remains useful for local development and import/export flows, but database-backed catalog configuration becomes the production authority.

## 6. Public APIs, Interfaces, and Types

### API Direction

The public contract is a typed `/api/v2` product surface separating public discovery, accounts, and ops concerns; the legacy `/api/v1` surface is retired.

### Public API Families

- `GET /api/v2/search`
- `GET /api/v2/media`
- `GET /api/v2/media/{id}`
- `GET /api/v2/media/{id}/mentions`
- `GET /api/v2/episodes`
- `GET /api/v2/episodes/{id}`
- `GET /api/v2/episodes/{id}/snippets`
- `GET /api/v2/podcasts`
- `GET /api/v2/podcasts/{slug}`
- `GET /api/v2/trends`
- `GET /api/v2/collections`
- `GET /api/v2/collections/{slug}`

### Auth and Account API Families

- `POST /api/v2/auth/magic-link/request`
- `POST /api/v2/auth/magic-link/verify`
- `POST /api/v2/auth/logout`
- `GET /api/v2/me`
- `GET /api/v2/me/saves`
- `POST /api/v2/me/saves`
- `DELETE /api/v2/me/saves/{id}`
- `GET /api/v2/me/follows`
- `POST /api/v2/me/follows`
- `DELETE /api/v2/me/follows/{id}`
- `GET /api/v2/me/alerts`
- `POST /api/v2/me/alerts`
- `PATCH /api/v2/me/alerts/{id}`
- `DELETE /api/v2/me/alerts/{id}`

### Ops and Admin API Families

- `GET /api/v2/ops/dashboard`
- `GET /api/v2/ops/podcasts`
- `POST /api/v2/ops/podcasts`
- `PATCH /api/v2/ops/podcasts/{id}`
- `POST /api/v2/ops/podcasts/{id}/archive`
- `GET /api/v2/ops/pipelines`
- `POST /api/v2/ops/pipelines/run`
- `GET /api/v2/ops/review-queue`
- `POST /api/v2/ops/review-queue/{id}/approve`
- `POST /api/v2/ops/review-queue/{id}/reject`
- `POST /api/v2/ops/review-queue/{id}/merge`
- `POST /api/v2/ops/review-queue/{id}/reclassify`
- `POST /api/v2/ops/review-queue/{id}/split`
- `POST /api/v2/ops/episodes/{id}/rerun`
- `GET /api/v2/ops/media/{id}/merge-preview`
- `GET /api/v2/ops/media/{id}`
- `PATCH /api/v2/ops/media/{id}`
- `POST /api/v2/ops/media/{id}/aliases`
- `POST /api/v2/ops/media/{id}/external-refs`
- `POST /api/v2/ops/media/{id}/split`
- `POST /api/v2/ops/media/{id}/merge`
- `GET /api/v2/ops/search`
- `POST /api/v2/ops/search/reindex`
- `POST /api/v2/ops/search/tuning/preview`
- `POST /api/v2/ops/search/tuning`
- `GET /api/v2/ops/retention/sampling`
- `POST /api/v2/ops/retention/sampling/recalculate`
- `GET /api/v2/ops/retention/transcripts`
- `POST /api/v2/ops/retention/transcripts/{id}/preview`
- `POST /api/v2/ops/retention/transcripts/{id}/evaluate`
- `POST /api/v2/ops/retention/transcripts/{id}/purge`
- `POST /api/v2/ops/retention/transcripts/{id}/reacquire`
- `POST /api/v2/takedown-requests`
- `GET /api/v2/ops/takedown-requests`
- `POST /api/v2/ops/takedown-requests/{id}/decision`
- `GET /api/v2/ops/audit-log`
- `GET /api/v2/admin/settings`
- `PATCH /api/v2/admin/settings`

### Key DTO Categories

#### Public Discovery Resources

- `SearchResultGroup`
- `MediaSummary`
- `MediaDetail`
- `MentionOccurrence`
- `TranscriptSnippet`
- `PodcastSummary`
- `PodcastDetail`
- `TrendSeries`
- `CollectionSummary`
- `CollectionDetail`

#### Account Resources

- `CurrentUser`
- `SavedItem`
- `FollowTarget`
- `AlertRule`
- `DigestPreference`
- `SessionInfo`

#### Ops, Review, and Pipeline Resources

- `PodcastSourceConfig`
- `PipelineRunSummary`
- `PipelineStepSummary`
- `ReviewQueueItem`
- `ReviewDecision`
- `CanonicalMergePreview`
- `AuditLogEntry`
- `ProviderHealthStatus`

### Contract Decisions

- Public API IDs are opaque at the boundary even if internal persistence uses numeric identifiers.
- Enums are centralized and shared across backend and generated frontend clients.
- Cursor pagination is used for mutable feeds; offset pagination is retained only where appropriate.
- Published catalog DTOs are distinct from review-state and candidate-state DTOs.
- Search and indexing are derived projections layered on top of published records.
- API contracts are generated and typed for frontend consumption to reduce drift.

## 7. Core Workflows

### Podcast Onboarding

1. Operator creates a podcast record and source configuration.
2. System validates connectors and stores health state.
3. Operator activates or pauses the podcast.
4. Scheduler includes only eligible sources in recurring runs.

### Episode Ingestion

1. Discovery adapters fetch episode candidates from configured sources.
2. Deduplication resolves the same episode across source providers.
3. Episode records are created or updated.
4. Transcript acquisition is scheduled for eligible episodes.

### Transcript Acquisition

1. Provider priority and fallback rules determine acquisition order.
2. Raw transcript artifacts are stored with provenance.
3. Cleanup generates normalized text and snippet-friendly segment structures.
4. Failures are recorded as stage-specific pipeline outcomes.

### Extraction and Canonicalization

1. Transcript content is chunked and sent through extraction logic.
2. Mention candidates are created with spans, confidence, and metadata.
3. Candidate resolution attempts to match existing canonical media or create new canonical records.
4. Candidate-state objects remain internal until publish policy is satisfied.

### Enrichment

1. Canonical media records are enriched via external providers.
2. External references, aliases, descriptions, images, and verification evidence are attached.
3. Confidence and provenance are preserved separately from raw provider payloads.

### Hybrid Review

1. High-confidence content can auto-publish when policy permits.
2. Ambiguous, conflicting, or low-confidence output enters a hybrid review queue.
3. Operators approve, reject, merge, split, or reclassify records.
4. Review decisions are auditable and reversible.

### Search and Index Projection

1. Published records emit indexing work.
2. Search documents are rebuilt or incrementally updated in Meilisearch.
3. Projection state is monitored independently from source-of-truth records.
4. Search analytics feed relevance tuning and editorial improvements.

### Alerting and Personalization

1. Users save and follow podcasts, media, or topics.
2. Published events are evaluated against alert rules.
3. Matching users receive digest or alert deliveries according to preferences.
4. Delivery outcomes are recorded for reliability and support workflows.

### Derivative Generation

1. Published transcript-derived content triggers derivative generation jobs.
2. Semantic chunks are produced from transcript windows with embeddings, short context snippets, and provenance back to episode and timestamp.
3. Graph triples are extracted to link canonical entities through typed relations with provenance.
4. Episode-level and media-level summaries are generated or refreshed from derivatives rather than from raw transcripts.
5. Derivative artifacts are persisted as first-class, idempotent records so that later retention decisions can treat raw transcripts as purgeable inputs.
6. Derivative jobs are replay-safe and carry model versions, prompt versions, and extraction lineage for auditability.

### Retention Decision

1. A scheduled retention evaluator inspects each transcript against its `PodcastSource` retention policy.
2. The evaluator computes whether minimum candidate confidence, multi-model stability, absence of open review items, cooling period, and derivative completeness thresholds are satisfied.
3. Eligible transcripts transition between hot, warm, cold, and purged states, with transitions recorded in the audit log.
4. Purge operations replace the raw transcript artifact with a `TranscriptDigest` proof-of-processing record while preserving derivatives and extraction history.
5. A stratified retention sample is exempt from purge so that future extraction model evaluation remains possible on a representative corpus.
6. Re-acquisition from upstream providers is used when a purged transcript must be reprocessed, with the resulting retention state starting fresh from hot.

### Takedown and Creator Opt-Out

1. Creators, rights holders, or operators submit a `TakedownRequest` for a podcast, episode, or specific mention.
2. The request enters the review queue with elevated priority and privileged visibility.
3. Operators can suppress raw transcripts, suppress derivative artifacts, unpublish mentions, and purge search projections through a single coordinated workflow.
4. Source-level opt-out flags on `PodcastSource` allow pre-emptive suppression of raw transcript retention while still permitting limited discovery metadata.
5. Every takedown decision is immutable in the audit log with before-and-after state captured for defensibility.

## 8. Frontend Direction

### Astro and React Role Split

Astro remains the primary framework for routing, server-rendered pages, SEO, and static-friendly public content. React is used for highly interactive surfaces such as search experiences, filter panels, account controls, and ops tooling.

### Feature and Module Organization

- `features/search`
- `features/media`
- `features/episodes`
- `features/podcasts`
- `features/collections`
- `features/accounts`
- `features/alerts`
- `features/ops`
- shared `ui`, `api`, `state`, `types`, and `routing`

### Route Map

#### Public Routes

- `/`
- `/search`
- `/media`
- `/media/:id`
- `/episodes`
- `/episodes/:id`
- `/sources`
- `/sources/:slug`
- `/stats`
- `/collections`
- `/collections/:slug`

#### Account Routes

- `/account`
- `/account/saved`
- `/account/follows`
- `/account/alerts`
- `/account/settings`

#### Ops Routes

- `/ops`
- `/ops/podcasts`
- `/ops/pipelines`
- `/ops/review`
- `/ops/media`
- `/ops/search`
- `/ops/audit`
- `/ops/settings`

### SSR vs Interactive Boundaries

- Server-render discovery pages that benefit from SEO, caching, and fast first paint.
- Use interactive React islands or app surfaces for search refinement, saved-state controls, account workflows, and ops interfaces.
- Keep data-fetching boundaries explicit so public pages, authenticated pages, and ops pages do not share accidental coupling.

### Typed API Client Usage

Frontend code consumes generated typed API clients and shared DTO contracts. Direct ad hoc fetch logic is minimized so public discovery, accounts, and ops surfaces remain contract-driven and refactor-friendly.

## 9. Non-Functional Requirements

### Performance

- Public search p95 under 400 ms.
- Media and episode detail p95 under 700 ms uncached.
- Index lag for published content under 5 minutes under normal operating conditions.
- Public pages optimized for SEO and fast first contentful paint.

### Reliability

- Pipeline stages are idempotent.
- Jobs can be retried safely.
- Stage failures do not corrupt published catalog state.
- Reruns are supported at the podcast, episode, and indexing levels.

### Security

- Public content is readable without login.
- Accounts use passwordless authentication with expiring magic links and secure sessions.
- Ops and admin actions use role-based access control.
- Privileged actions are audited.
- Rate limits protect public search, auth, and ops endpoints.
- Secrets are stored in environment or secret-management systems, never in tracked source files.
- Raw transcripts are ops-only, encrypted at rest in object storage, and never exposed through public APIs.
- Public surfaces serve short transcript snippets around mentions rather than full transcripts, with deep links back to the original source.
- A formal takedown intake endpoint exists and is reachable without an account.

### Privacy

- User data collection is minimal and purpose-driven.
- Notification preferences are explicit and reversible.
- Audit and delivery records follow retention rules.
- Public product behavior does not depend on invasive user profiling.
- Creator opt-out preferences are honored at the podcast-source level and cascade to transcript retention, derivative generation, and public surfaces.
- Transcript retention follows explicit policy-driven lifecycles rather than indefinite storage, and every retention transition is auditable.
- Personally identifying content that appears incidentally in transcripts is governed by the same takedown and retention policies as copyrighted source material.

### Accessibility

- Keyboard-first navigation across public and ops surfaces.
- Clear focus states, labels, and semantic structure.
- WCAG 2.2 AA target for key routes and workflows.
- Confidence and provenance states presented in accessible language.

### Observability

- Structured logs for API, worker, and projection layers.
- Metrics for discovery yield, pipeline latency, review backlog, search lag, and delivery success.
- Tracing across ingestion, extraction, enrichment, and indexing boundaries.
- Alerting on failed providers, queue buildup, and degraded indexing.

## 10. Testing and Acceptance Scenarios

Cross-cutting backend test coverage against these scenarios is tracked under #20, which spans every phase rather than being grouped under a single phase parent. The first v2 discovery and ops activity slices already landed; remaining coverage lands incrementally alongside the relevant feature work.

### Unit Tests

- Episode deduplication across multiple source providers.
- Candidate scoring and publish policy evaluation.
- Canonical merge rules and alias behavior.
- Account permission and role policies.
- DTO serialization and enum stability.

### API Tests

- Public discovery endpoints and filtering behavior.
- Auth and account lifecycle flows.
- Saves, follows, and alerts CRUD behavior.
- Ops review decisions and rerun endpoints.
- Explicit verification that retired `/api/v1` routes remain unavailable.

### Integration Tests

- End-to-end ingestion from discovery through published record.
- Transcript provider fallback behavior.
- Search projection updates after publish or review approval.
- Media merge preserving history, aliases, and references.
- YAML-based initial import into database-backed catalog management.

### Frontend Tests

- Search and filter interactions.
- Saved-state and follow-state flows.
- Authenticated account pages.
- Protected ops navigation and role gates.
- Error, empty, and degraded backend states.

### End-to-End Scenarios

- Operator adds a new podcast and its first indexed episodes appear in discovery.
- Ambiguous extraction enters review and remains hidden from the public product.
- Approved content appears in media detail, episode detail, and search projection.
- User follows an item and receives a configured digest after a new published mention.
- Operator merges duplicate media records without breaking canonical discovery paths.

### Data Quality Acceptance Targets

- Auto-published high-confidence output maintains precision of at least 95 percent.
- Every public mention includes episode provenance and usable context.
- Unresolved candidate-state content never appears in published discovery surfaces.
- Review actions and canonical merges are auditable and reversible.

## 11. Implementation Sequence

### Phase 1: Boundary Hardening

- **Status:** Complete
- **Retro parents:** #31 (v2 API foundation), #32 (shared services), #33 (v2 public discovery), #34 (v2 ops + admin), #36 (backend tests)
- **Completed:**
  - [x] Reorganize selected backend API logic into shared application-style services. (#32)
  - [x] Introduce clearer contracts for the first public and ops v2 surfaces. (#31, #33, #34)
  - [x] Establish the initial `/api/v2` direction without breaking the existing product. (#37)
  - [x] Add the initial v2 media contract with opaque IDs and shared v1/v2 media services. (#47, #39)
  - [x] Add grouped v2 search backed by shared discovery services and opaque public IDs. (#44, #39)
  - [x] Refactor the existing v1 search surface to reuse the shared discovery service layer. (#43)
  - [x] Add typed v2 public episode list/detail/mentions contracts backed by shared episode query services. (#45, #39)
  - [x] Refactor the existing v1 episode surface to reuse the shared episode service layer. (#43)
  - [x] Add a typed v2 public trends contract backed by shared discovery stats query services. (#46)
  - [x] Add a typed v2 ops pipeline activity contract backed by shared run and job query services. (#48, #40)
  - [x] Add a typed v2 ops pipeline run contract backed by shared run mutation services. (#48, #40)
  - [x] Add a typed v2 ops search projection contract backed by shared search projection query services. (#49, #41)
  - [x] Add a typed v2 ops podcast catalog contract backed by shared podcast management query and mutation services, including archive and clear-field updates. (#50)
  - [x] Add a typed v2 ops episode rerun contract backed by shared job mutation services. (#51)
  - [x] Add a typed v2 ops media merge contract backed by shared media merge services. (#51)
  - [x] Add typed v2 ops review queue contracts backed by shared review queue services and review-state models. (#52, #42)
  - [x] Add typed v2 ops audit log contracts backed by shared audit log services and audit-state models. (#53, #42)
  - [x] Add typed v2 admin settings contracts backed by shared runtime configuration services. (#53)
  - [x] Introduce review-state and audit-state domain models so review queue and audit log surfaces can be added without faking missing workflows. (#42, #52, #53)

### Phase 2: Catalog and Pipeline Maturity

- **Status:** Complete
- **Retro parent:** #35 (candidate-backed extraction workflow)
- **Roadmap parent:** #59 (Phase 2 implementation)
- **Completed:**
  - [x] Route extracted mentions into replay-safe review candidates instead of publishing immediately. (#54)
  - [x] Persist transcript-derived timestamp/context spans and extraction-job provenance on candidates. (#55)
  - [x] Preserve immutable candidate provenance history across reruns and review-side reclassification. (#55)
  - [x] Surface recent extraction job history, including failures, directly in the ops review queue. (#56)
  - [x] Make review publish and merge projection updates best-effort and replay-safe so search docs stay aligned without blocking writes. (#56)
  - [x] Track pending search projection repairs after extract reruns so ops can see reindex work for already-published episodes and media. (#56)
  - [x] Add a pure interval scheduler planning foundation for ingestion, reindex, and digest work items. (#11)
  - [x] Add pure transcript retention policy primitives for lifecycle tiers, purge blockers, source opt-out, and retention sampling. (#9, #27, #74)
  - [x] Add pure canonical media alias matching primitives for title/alias normalization and type-compatible exact matches. (#12)
  - [x] Add TTL-backed public stats, trends, and top-mentioned read-model caching with catalog-signature invalidation. (#15)
  - [x] Add persisted pipeline schedules and scheduled work items with an ops-visible v2 scheduled-work surface. (#11)
  - [x] Promote active-podcast episode discovery into recurring scheduled ingestion work with ingestion-run execution tracking. (#65, #11)
  - [x] Add transcript retention columns plus retention evaluation and raw-transcript purge commands. (#9, #27, #74)
  - [x] Add persisted media aliases and wire alias-backed canonical matching into review publish and merge decisions. (#12)
- **Completed follow-through (tracked under #59):**
  - [x] Promote episode discovery + dedup into recurring ingestion workflows and clearer operator visibility. (#65)
  - [x] Finish retention-aware transcript acquisition provider hooks and lifecycle automation. (#9)
  - [x] Add recurring reindex + digest execution on top of the shared schedule/work-item model. (#11)
  - [x] Finish canonical media resolution merge previews and broader alias ergonomics. (#12)
  - [x] Operationalize media enrichment with external references and verification in the main pipeline. (#13)
  - [x] Add caching for stats, trends, and top-list surfaces. (#15)
  - [x] Add endpoint-level rate limiting for public search, auth, and ops endpoints. (#16)
  - [x] Finalize staging deployment configuration. (#21)

### Phase 2.5: Derivative Data Layer

- **Status:** Complete
- **Roadmap parent:** #60
- **Work items:**
  - [x] Semantic chunks + embeddings pipeline on Postgres + pgvector. (#24)
  - [x] Graph triples + entity relations. (#25)
  - [x] Per-episode and per-media summary models. (#66)
  - [x] Derivative generation pipeline orchestration with idempotent, replay-safe, version-tracked behavior. (#67)
  - [x] Time-boxed LightRAG spike evaluating hybrid graph-plus-vector retrieval vs. a hand-rolled layer. (#26)
  - [x] Wrap adopted retrieval framework behind ports.
  - [x] Prove derivative coverage is sufficient for all public query classes before retention purge activates.

### Phase 3: Ops Console

- **Status:** Complete
- **Roadmap parent:** #61
- **Work items:**
  - [x] Ops dashboard (pipelines + queue + provider health). (#68)
  - [x] Podcast manager UI (add, edit, pause, archive). (#69)
  - [x] Pipeline run inspection views. (#70)
  - [x] Review queue UI (approve, reject, merge, split, reclassify). (#71)
  - [x] Canonical media management UI (merge preview, aliases, metadata, split recovery). (#72)
  - [x] Search tooling UI (reindex, synonym tuning, projection diagnostics). (#73)
  - [x] Stratified retention sampling algorithm + implementation. (#74)
  - [x] Tiered transcript retention + confidence-gated purge (hot/warm/cold/purged + `TranscriptDigest`). (#27)
  - [x] DMCA takedown intake + creator opt-out registry. (#28)
- **#27 complete:** Lifecycle preview/evaluation/purge, durable digests, encrypted filesystem and S3-compatible artifact adapters, purge-time artifact deletion, audited re-acquisition into a new hot transcript, persisted source-scoped policies, future-acquisition opt-out enforcement, scheduled policy evaluation, and artifact-backed cleanup/extraction processing are implemented.
- **#28 complete:** Public takedown submission, privileged ops review/decision state, audit logging, ops intake controls, approved suppression of raw artifacts and derivatives/mentions, search projection removal, and source-level creator opt-out registration are implemented.

### Phase 4: Account Product

- **Status:** In progress
- **Roadmap parent:** #62
- **Work items:**
  - [x] Passwordless magic-link auth + sessions. (#75)
  - [x] Saves CRUD. (#76)
  - [x] Follows CRUD. (#77)
  - [x] Alerts CRUD + rule engine. (#78)
  - [x] Digests + notification delivery. (#79)
  - [x] Account pages (saved, follows, alerts, settings). (#80)
  - [x] Terms of Service + Privacy Policy pages. (#81)
  - [ ] Pre-launch legal review engagement. (#82)
  - [x] Paid subscription tier + per-user quota. (#30)
  - [x] Personalization features connect only to published catalog records.
  - [ ] Completed legal review + published policy pages treated as explicit gates for paid-tier launch.
- **#75 complete:** Configurable SMTP magic-link delivery, hashed single-use challenges, secure revocable session cookies, authenticated `/api/v2/me`, and sign-in/verification/account-entry pages are implemented.
- **#76 complete:** Authenticated saved-reference CRUD persists only public catalog media, with media-detail save toggles and account-page list/removal controls.
- **#77 complete:** Authenticated followed-source CRUD persists only public podcasts, with source-page follow toggles and account-page list/removal controls.
- **#78 complete:** Alert rule CRUD and deterministic evaluation monitor saved media for new mentions and followed podcasts for new episodes, producing durable events only when public counts advance.
- **#79 complete:** SMTP-backed digest delivery consumes generated alert events exactly once, records delivered digest history, and exposes account delivery controls.
- **#80 complete:** Dedicated account overview, saved, following, alerts, and settings destinations are wired to persisted notification preferences, including digest enablement and frequency controls.
- **#81 complete:** Public Terms of Service and Privacy Policy pages are published in the site shell with persistent footer entry points; their content remains subject to the explicit legal-review gate.
- **#82 prepared, awaiting external action:** `docs/legal-review-brief.md` captures the evidence package, review questions, sign-off record, and paid-launch gate; written counsel advice is required before this item can be checked complete.
- **#30 complete:** Launch-gated hosted subscription state, external checkout adapter boundary, monthly API/LLM quota contracts, paid-feature enforcement, account membership presentation, and public pricing page are implemented; paid enrollment is disabled by default pending review.
- **Next:** Continue into Phase 5 public product polish while the non-code legal review gate remains pending. (#17, #18)

### Phase 5: Public Product Polish

- **Status:** Complete
- **Roadmap parent:** #63
- **Work items:**
  - [x] Loading, empty, and degraded-state components across public + ops surfaces. (#17)
  - [x] v2 search experience with typed client + filters + URL state. (#18)
  - [x] Editorial collections experience. (#83)
  - [x] Detail page provenance + related entities + explanation layers. (#84)
  - [x] Ranking + search relevance tuning. (#85)
  - [x] SEO + sitemap + structured data. (#86)
- **#17 complete:** Shared Astro and React state panels now surface consistent empty, loading, and degraded behavior across primary discovery, account, and ops routes, including visible home-page degradation when upstream data is unavailable.
- **#18 complete:** `/search` consumes grouped typed v2 search results, offers references/episodes/sources filters, preserves query/filter URL state, and is linked from the public shell and search controls.
- **#83 complete:** Published-only editorial collections are persisted with ordered public media placements, exposed through typed v2 list/detail endpoints, and rendered as linked public collection experiences.
- **#84 complete:** Ready derivative summaries and typed graph relationships now project into public media and episode details, with user-facing explanation, source evidence, and related-reference sections.
- **#85 complete:** Existing reviewed index-tuning controls are now informed by anonymous normalized public query and result-selection signals, including an ops relevance report for empty-result and selection patterns.
- **#86 complete:** Canonical and social metadata are centralized in the public shell, entity detail pages publish JSON-LD, and deployable `robots.txt` and `sitemap.xml` routes expose static and API-backed discovery pages.
- **Next:** Keep paid activation disabled until external legal review is recorded, and obtain approval before any open-core relicensing.

### Phase 6: Stabilization

- **Status:** Complete
- **Roadmap parent:** #64
- **Work items:**
  - [x] Load test core search + ingestion paths. (#87)
  - [x] Metrics for review throughput, projection lag, and alert delivery. (#88)
  - [x] Alerting + operational playbooks. (#89)
  - [x] Retire legacy `/api/v1` surface. (#90)
- **#87 complete:** A bounded-concurrency HTTP load harness exercises public search and ingestion activity reads with latency/error thresholds, while opt-in pipeline triggers and a staging runbook cover intentional write-path testing without silently creating work.
- **#88 complete:** A typed ops metrics endpoint and dashboard panels report 24-hour review throughput, pending projection age/failures, and generated/delivered/pending account notification activity.
- **#89 complete:** Threshold-driven operational alerts surface review backlog, projection lag/failures, and notification-delivery backlog in the ops dashboard, with configurable limits and documented response playbooks.
- **#90 complete:** The application no longer mounts legacy `/api/v1` routes, obsolete router modules and endpoint tests are removed, v2 validation remains covered, and a regression assertion preserves the retirement contract.
- **Next:** Keep paid activation and licensing changes gated on the documented external approvals.

### Foundational pre-work

- **Status:** Evaluation complete; adoption pending approval
- **Roadmap parent:** #58
- **Work items:**
  - [x] Open-core license and repo split evaluation with recorded recommendation. (#29)
  - [ ] Owner/legal approval and adoption of any change from the existing MIT license.

`docs/open-core-license-evaluation.md` recommends AGPL for future open-core code and retaining a boundary-defined monorepo initially. The current MIT license remains unchanged unless and until ownership and legal review authorize a new licensing release.

## 12. Assumptions and Defaults

- Podex is a curated catalog, not an open ingestion marketplace.
- Public browsing works without authentication.
- Accounts are optional and focused on saves, follows, alerts, and digests.
- Authentication uses passwordless email magic links.
- Ops and admin roles are separate from public accounts and use stronger access controls.
- Postgres is the source of truth.
- Meilisearch is a search projection, not a canonical store.
- Redis-backed async processing powers queues and short-lived coordination.
- Astro remains the web framework for the primary frontend shell.
- The architectural default is a modular monolith.
- Refactorability and decoupled implementations are core design constraints.
- Podex follows an open-core distribution model. Core ingestion, extraction, data model, API contracts, and frontend shell are published under a copyleft license such as AGPL or BSL, while curated catalog data, enrichment prompts, ranking tuning, ops console polish, and hosted infrastructure remain closed.
- A hosted subscription tier covers infrastructure and LLM costs. Free public discovery remains available, while authenticated features such as saves, follows, alerts, digests, and the public API sit behind the subscription boundary with per-user quotas.
- Extracted derivatives are treated as the durable product surface and the primary moat, while raw transcripts are treated as a purgeable intermediate governed by retention policy.
- Raw transcripts are never a public contract. Public surfaces expose only structured mentions, short context snippets, and derivative artifacts.
- Legal review is expected before the first paid user, covering copyright, fair-use posture, DMCA workflow, and privacy disclosures.
- The default repository layout is a single monorepo with per-package license headers and `LICENSE` files. A split into separate open-core and closed repositories is a deliberate, tracked decision rather than an implicit one.
- Open-core license selection follows explicit criteria: a strong copyleft option such as AGPL is preferred when discouraging hosted clones and maximizing contribution reciprocity matter more than adoption breadth, while a Business Source License is preferred when a time-delayed conversion to a permissive license and clearer commercial carve-outs matter more. The decision is recorded alongside the license files and referenced from `README.md`.
