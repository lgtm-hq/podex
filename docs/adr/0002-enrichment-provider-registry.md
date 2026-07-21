<!-- markdownlint-disable MD041 -->
# 0002. Enrichment provider registry

- Status: accepted
- Date: 2026-07-21
- Tags: enrichment, config, providers

## Context

`MediaEnricher.__init__` hand-wired each enrichment provider behind five
optional API-key kwargs, with a second wiring site inside
`AcademicEnricher`. No production call site passed keys, and
`config.py` had no enrichment fields — so TMDB, OMDB, keyed PubMed, and
CrossRef polite-pool behavior were dead in production. Provider
`BASE_URL`s were unconfigurable class constants, blocking mock-server
testing. Adding a provider meant constructor churn in two places.

A grilling session (2026-07-20) settled that enrichment should follow
the nested-settings foundation (ADR 0001 / #353): a declarative
registry table driven by `settings.enrichment`, with a clean-break
constructor. Companion work (#346 MediaType routing, #350 ADR practice)
cleared the path for this change.

## Decision

1. **Declarative registry table**, private to `media_enrichment.py`: a
   frozen `ProviderSpec` per source (source, provider class, settings
   accessor, `requires_key`). No decorator self-registration, no entry
   points, no plugin loader. New provider = one spec line + one
   settings sub-model.
2. **Availability vs ordering stay separate.** The registry decides
   which providers are built (enabled + key present when required).
   `PROVIDER_PRIORITY` alone decides try-order per `MediaType`.
3. **Clean-break constructor:**
   `MediaEnricher(settings=None, providers=None)`. `settings=None`
   reads `get_settings().enrichment`. `providers=` bypasses the
   registry for tests and does not transfer ownership (injected clients
   are not closed). The five key kwargs are deleted.
4. **Settings-driven keys and base URLs** under
   `PODEX_ENRICHMENT__<PROVIDER>__*`. One small BaseModel per provider
   (`enabled`, `base_url`, and `api_key` or CrossRef `mailto`). TMDB
   Person shares `settings.enrichment.tmdb`. No rate-limit settings —
   per-API limits remain hard-coded on providers.
5. **Conservative defaults:** empty keys / default env → byte-identical
   to today's keyless set (Google Books, Open Library, iTunes,
   Wikipedia, plus academic PubMed / Semantic Scholar / CrossRef).
   `requires_key=True` only for TMDB, TMDB Person, and OMDB. SPOTIFY
   remains an intentional registry exclusion (no provider class).
6. **Single ownership path:** the registry builds academic providers
   too; `AcademicEnricher(providers=...)` is orchestration-only.
   `MediaEnricher` owns lifecycle when it builds from the registry and
   closes both its map and the academic map once.

Deliberate non-choices: plugin/entry-point discovery; dual-read of the
old key kwargs; exposing rate limits via env; registering SPOTIFY
without an implementation.

## Consequences

- Keyed enrichment is deployable via env for the first time, off by
  default until keys are set.
- Ops can disable a provider or repoint `base_url` without a deploy;
  tests inject fakes without patching imports or close-then-swap.
- Adding a provider no longer touches the `MediaEnricher` constructor
  signature; exhaustiveness tests guard the `EnrichmentSource` enum
  against silent omissions.
- Call sites and tests must migrate in the same change (hard cutover).
  Bare `AcademicEnricher()` no longer self-builds providers — pass an
  explicit map or obtain providers via `MediaEnricher`.
