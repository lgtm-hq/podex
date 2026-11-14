# Architecture Decision Records

This directory records decisions with lasting architectural consequence for
Podex. ADRs answer _why_ the system is shaped the way it is; issues, PRs, and
the changelog record _what_ changed.

## Index

| Number                                                  | Title                                                           | Status   | Date       |
| ------------------------------------------------------- | --------------------------------------------------------------- | -------- | ---------- |
| [0001](./0001-nested-settings-models.md)                | Nested settings models with hard cutover                        | accepted | 2026-07-20 |
| [0002](./0002-enrichment-provider-registry.md)          | Enrichment provider registry                                    | accepted | 2026-07-21 |
| [0003](./0003-bounded-public-feed-http.md)              | Bounded public feed HTTP boundary                               | proposed | 2026-11-14 |
| [0004](./0004-keyset-sweep-checkpoints.md)              | Keyset checkpoints for recurring batched sweeps                 | proposed | 2026-11-14 |
| [0005](./0005-verified-transcript-purge-proof.md)       | Transcript purge proof identifies a verified source             | proposed | 2026-11-14 |
| [0006](./0006-transcript-bound-retention-confidence.md) | Retention confidence comes from the transcript's own extraction | proposed | 2026-11-14 |
| [0007](./0007-postgres-test-lane.md)                    | PostgreSQL test lane with a migrated database per test          | proposed | 2026-11-14 |
| [0008](./0008-masked-secret-settings.md)                | Secret settings are masked types; reports redact free text      | proposed | 2026-11-14 |
| [0009](./0009-ingestion-failure-descriptors.md) | Persist allowlisted ingestion failure descriptors | proposed | 2026-11-14 |

## Format

Use [template.md](./template.md). Fields:

- **Title** — short, imperative or noun phrase (`NNNN-short-slug.md`).
- **Status** — `proposed`, `accepted`, `superseded by NNNN`, or
  `accepted (backfilled)` for historical decisions written after the fact.
- **Context** — the forces that made the decision necessary.
- **Decision** — what we chose (and deliberately did not choose).
- **Consequences** — trade-offs and follow-on constraints.

## Lifecycle

1. Draft an ADR as `proposed` in the same PR that implements the decision (or in
   a docs-only PR when the decision precedes code).
2. Mark it `accepted` when the implementing PR merges (or when the owning change
   is already on `main` for a backfill).
3. Never edit an accepted ADR's Decision. To change course, write a new ADR that
   supersedes the old one and update the old Status line.

## When to write an ADR

Write an ADR when a choice:

- Has lasting architectural consequence (shape of settings, provider wiring,
  auth/billing vendors, storage contracts, schema conventions).
- Is not obvious from the code alone, or is likely to be re-litigated.
- Comes out of a grilling session that settles design.

Do **not** ADR routine refactors, bug fixes, or choices already fully specified
by an existing accepted ADR.

See also the ADR-first convention in [`CONTRIBUTING.md`](../../CONTRIBUTING.md)
and [`AGENTS.md`](../../AGENTS.md).
