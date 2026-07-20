# Architecture Decision Records

This directory records decisions with lasting architectural consequence
for Podex. ADRs answer *why* the system is shaped the way it is; issues,
PRs, and the changelog record *what* changed.

## Index

| Number | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](./0001-nested-settings-models.md) | Nested settings models with hard cutover | accepted | 2026-07-20 |
| 0002 | Enrichment provider registry | proposed | — |

Number **0002** is reserved for the enrichment-registry epic
(`0002-enrichment-provider-registry.md`). Its body file lands with the
owning implementation PR; do not create a placeholder body ahead of that
PR. Link the row when the file lands.

## Format

Use [template.md](./template.md). Fields:

- **Title** — short, imperative or noun phrase (`NNNN-short-slug.md`).
- **Status** — `proposed`, `accepted`, `superseded by NNNN`, or
  `accepted (backfilled)` for historical decisions written after the
  fact.
- **Context** — the forces that made the decision necessary.
- **Decision** — what we chose (and deliberately did not choose).
- **Consequences** — trade-offs and follow-on constraints.

## Lifecycle

1. Draft an ADR as `proposed` in the same PR that implements the
   decision (or in a docs-only PR when the decision precedes code).
2. Mark it `accepted` when the implementing PR merges (or when the
   owning change is already on `main` for a backfill).
3. Never edit an accepted ADR's Decision. To change course, write a new
   ADR that supersedes the old one and update the old Status line.

## When to write an ADR

Write an ADR when a choice:

- Has lasting architectural consequence (shape of settings, provider
  wiring, auth/billing vendors, storage contracts, schema conventions).
- Is not obvious from the code alone, or is likely to be re-litigated.
- Comes out of a grilling session that settles design.

Do **not** ADR routine refactors, bug fixes, or choices already fully
specified by an existing accepted ADR.

See also the ADR-first convention in [`CONTRIBUTING.md`](../../CONTRIBUTING.md)
and [`AGENTS.md`](../../AGENTS.md).
