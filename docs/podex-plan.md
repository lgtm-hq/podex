# Podex End-State Product Plan

## 1. Summary

Podex is a curated multi-podcast product that turns podcast conversations into a structured, trustworthy discovery graph for media, people, places, studies, and related entities. The product is designed to help users answer not just what was mentioned, but also where it was mentioned, when it was mentioned, who mentioned it, how often it appears, and how confident the system is in the underlying data.

The end-state product has a public + ops shape. The public side is a fast discovery experience for browsing, searching, filtering, and understanding podcast-driven recommendations. The ops side is a private operational platform for source onboarding, ingestion monitoring, review workflows, metadata correction, search tuning, and auditability.

Architecturally, Podex is a modular monolith with decoupled boundaries. The system keeps business domains isolated behind stable interfaces so that refactoring remains localized and eventual service extraction remains possible without rewriting the product. The design favors explicit contracts, projection-driven read models, and pipeline stages with clear responsibilities over tightly coupled CRUD layers.

Trust is a product feature, not a backend detail. Podex emphasizes provenance, confidence scoring, hybrid review, canonical entity management, and operator visibility into every stage of the pipeline. Search, stats, and discovery surfaces are powered by published catalog records and search projections, while uncertain candidates stay isolated until policy or operator review makes them safe to publish.

## Progress Snapshot

### Current Status

- **Overall status:** Phase 1 complete and Phase 2 underway with candidate-backed extraction workflow grounding.
- **Current focus:** Phase 2 catalog and pipeline maturity work.

### Completed So Far

- [x] Added `/api/v2` routing and application wiring.
- [x] Added a dedicated v2 public API foundation.
- [x] Added a dedicated v2 ops API foundation.
- [x] Added a first ops dashboard endpoint.
- [x] Added shared catalog/status query services so v1 and v2 can reuse backend logic.
- [x] Added shared media query services so v1 and v2 can reuse media/detail logic.
- [x] Added shared public search services so v1 and v2 can reuse discovery logic.
- [x] Added shared public episode query services so v1 and v2 can reuse episode/detail logic.
- [x] Refactored existing v1 podcast, status, search, and episode routes to use shared services.
- [x] Added grouped `/api/v2/search` with opaque IDs and future-facing public resource URLs.
- [x] Added typed `/api/v2/episodes` list/detail/mentions endpoints with opaque IDs and nested media references.
- [x] Added typed `/api/v2/trends` discovery coverage for overview, by-type, and top-mentioned views.
- [x] Added a dedicated `/api/v2/ops/pipelines` contract for recent runs and transcription-job activity.
- [x] Added a dedicated `/api/v2/ops/pipelines/run` contract for operator-triggered pipeline runs.
- [x] Added a dedicated `/api/v2/ops/search` contract for search projection health and per-index stats.
- [x] Added typed `/api/v2/ops/podcasts` catalog management coverage for filtered inventory plus create/update/archive flows.
- [x] Added a dedicated `/api/v2/ops/episodes/{id}/rerun` contract for replaying selected processing jobs.
- [x] Added a dedicated `/api/v2/ops/media/{id}/merge` contract for canonicalizing duplicate media records.
- [x] Added a dedicated `/api/v2/ops/review-queue` contract backed by review-state models and shared review services.
- [x] Added a dedicated `/api/v2/ops/audit-log` contract backed by audit-state models and real privileged-action logging.
- [x] Added typed `/api/v2/admin/settings` coverage for runtime configuration snapshots and partial updates.
- [x] Added shared pipeline query services so ops surfaces do not depend on raw ORM access patterns.
- [x] Added shared pipeline mutation services so ops run and rerun surfaces do not depend on raw ORM writes.
- [x] Added shared search projection query services so ops surfaces do not depend on direct Meilisearch client access.
- [x] Added shared review queue services so review surfaces can publish, reject, and merge candidates without faking workflow state.
- [x] Added shared audit log services so ops and admin mutations land in a unified audit stream.
- [x] Routed transcript extraction output into replay-safe mention candidates and operator review items instead of directly publishing new mentions.
- [x] Added a typed review reclassification mutation so pending candidates can be corrected before approval while preserving provenance and audit history.
- [x] Added backend tests covering the first v2 discovery and ops activity slices.

### In Progress

- [x] Expand the v2 public surface with media-focused endpoints.
- [x] Expand the v2 public surface with search-focused endpoints.
- [x] Continue formalizing background job boundaries and projection-oriented contracts with a typed ops pipeline activity endpoint.
- [x] Continue broadening `/api/v2` discovery coverage beyond the first catalog and search surfaces with typed episode list/detail/mentions endpoints.
- [x] Continue separating projection and background-job concerns behind dedicated shared services with a typed ops search projection endpoint.
- [x] Continue broadening `/api/v2` discovery coverage into richer discovery surfaces such as trends, collections, or related-entity views.
- [x] Begin Phase 2 extraction workflow hardening by persisting replay-safe mention candidates and review queue items from transcript extraction.

### Not Started

- [ ] Phase 3: Ops Console
- [ ] Phase 4: Account Product
- [ ] Phase 5: Public Product Polish
- [ ] Phase 6: Stabilization

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

### Publication Lifecycle

Podex uses an explicit lifecycle for extracted content:

1. **Candidate** for newly extracted and untrusted output.
2. **Published** for records that are safe to expose publicly.
3. **Retired or merged** for superseded, hidden, or consolidated records that still need historical traceability.

### Transcript Storage Direction

- Keep transcript metadata and searchable snippet records in Postgres.
- Store large raw transcript artifacts and bulky derived payloads in object storage.
- Track provider, cleanup lineage, extraction provenance, and timestamps for every transcript asset.

### Source Catalog Management Direction

Podcast and source configuration evolves from YAML-based initial import toward database-backed management. YAML remains useful for local development and import/export flows, but database-backed catalog configuration becomes the production authority.

## 6. Public APIs, Interfaces, and Types

### API Direction

The public contract evolves from a mostly `/api/v1` CRUD-oriented surface to a clearer `/api/v2` product contract that separates public discovery, accounts, and ops concerns.

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
- `POST /api/v2/ops/episodes/{id}/rerun`
- `POST /api/v2/ops/media/{id}/merge`
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

### Privacy

- User data collection is minimal and purpose-driven.
- Notification preferences are explicit and reversible.
- Audit and delivery records follow retention rules.
- Public product behavior does not depend on invasive user profiling.

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
- Backward compatibility expectations while `/api/v1` remains active.

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
- **Completed:**
  - [x] Reorganize selected backend API logic into shared application-style services.
  - [x] Introduce clearer contracts for the first public and ops v2 surfaces.
  - [x] Establish the initial `/api/v2` direction without breaking the existing product.
  - [x] Add the initial v2 media contract with opaque IDs and shared v1/v2 media services.
  - [x] Add grouped v2 search backed by shared discovery services and opaque public IDs.
  - [x] Refactor the existing v1 search surface to reuse the shared discovery service layer.
  - [x] Add typed v2 public episode list/detail/mentions contracts backed by shared episode query services.
  - [x] Refactor the existing v1 episode surface to reuse the shared episode service layer.
  - [x] Add a typed v2 public trends contract backed by shared discovery stats query services.
  - [x] Add a typed v2 ops pipeline activity contract backed by shared run and job query services.
  - [x] Add a typed v2 ops pipeline run contract backed by shared run mutation services.
  - [x] Add a typed v2 ops search projection contract backed by shared search projection query services.
  - [x] Add a typed v2 ops podcast catalog contract backed by shared podcast management query and mutation services, including archive and clear-field updates.
  - [x] Add a typed v2 ops episode rerun contract backed by shared job mutation services.
  - [x] Add a typed v2 ops media merge contract backed by shared media merge services.
  - [x] Add typed v2 ops review queue contracts backed by shared review queue services and review-state models.
  - [x] Add typed v2 ops audit log contracts backed by shared audit log services and audit-state models.
  - [x] Add typed v2 admin settings contracts backed by shared runtime configuration services.
  - [x] Introduce review-state and audit-state domain models so review queue and audit log surfaces can be added without faking missing workflows.

- Reorganize backend around domain modules, application services, and ports.
- Introduce clearer contracts for public, account, and ops surfaces.
- Establish the `/api/v2` direction without breaking the existing product.
- Formalize background job boundaries and projection flows.

### Phase 2: Catalog and Pipeline Maturity

- **Status:** In progress
- **Completed so far:**
  - [x] Route extracted mentions into replay-safe review candidates instead of publishing immediately.
  - [x] Persist transcript-derived timestamp/context spans and extraction-job provenance on candidates.
  - [x] Preserve immutable candidate provenance history across reruns and review-side reclassification.
  - [x] Surface recent extraction job history, including failures, directly in the ops review queue.
  - [x] Make review publish and merge projection updates best-effort and replay-safe so search docs stay aligned without blocking writes.
  - [x] Track pending search projection repairs after extract reruns so ops can see reindex work for already-published episodes and media.

- Move podcast and source management toward database-backed authority.
- Introduce explicit candidate, review, and pipeline stage models.
- Strengthen transcript, extraction, enrichment, and search projection workflows.
- Add replay-safe ingestion and indexing behavior.

### Phase 3: Ops Console

- **Status:** Not started

- Build the ops dashboard, podcast manager, run inspection views, and review queue.
- Add merge, dedupe, rerun, and audit workflows.
- Expose provider health and quality thresholds to operators and admins.

### Phase 4: Account Product

- **Status:** Not started

- Add passwordless auth, sessions, saves, follows, alerts, and digest preferences.
- Build account-facing pages and notification delivery behavior.
- Connect personalization features only to published catalog records.

### Phase 5: Public Product Polish

- **Status:** Not started

- Add richer discovery pages, editorial collections, and trend surfaces.
- Improve detail pages with stronger provenance, related entities, and explanation layers.
- Tune ranking, search relevance, and SEO behavior.

### Phase 6: Stabilization

- **Status:** Not started

- Load-test core search and ingestion paths.
- Measure review throughput, projection lag, and alert delivery quality.
- Harden observability, reliability, and operational playbooks.
- Retire legacy surface areas when replacement contracts are complete.

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
