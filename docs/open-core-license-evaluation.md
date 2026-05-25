# Podex Open-Core License and Repository Evaluation

Status: engineering evaluation complete; owner and legal approval required before any license change.

## Existing State

Podex currently ships under the MIT license in the repository root. This evaluation
does not modify that license or change rights already granted under it.

## Recommendation

Adopt an AGPL-3.0-or-later open-core boundary for future distributable core code,
subject to confirmation that the project owns or can relicense all affected code.
Keep a single monorepo while the product remains a modular monolith.

This recommendation fits the stated product goal of making the core transparent
while requiring hosted modifications to the core to remain available under the
same license. A BSL approach offers a stronger hosted-service commercial
restriction and eventual conversion, but it is source-available rather than an
OSI open-source license and introduces conversion-date and additional-use-grant
administration.

## Proposed Boundary

| Area | Proposed treatment |
| --- | --- |
| Public catalog API contracts, extraction pipeline, core data model, and public frontend shell | AGPL-licensed core |
| Curated catalog data and raw transcript artifacts | Excluded data assets; governed separately |
| Proprietary enrichment prompts, ranking configuration, hosted billing/infrastructure configuration, and operator procedures | Private hosted assets/services |
| Third-party dependencies and imported metadata | Preserve upstream licenses and attribution requirements |

The ops UI should be reviewed feature by feature before publishing: generic
catalog/review tooling can belong to core, while hosted operational policy and
deployment material can remain private.

## Repository Layout Decision

Retain the monorepo initially, with explicit directory-level ownership and
licensing boundaries once approved. This keeps shared schemas, migrations, and
API changes atomic while boundaries are still moving quickly.

A split repository becomes appropriate only when private hosted modules need
independent access controls or releases, or when shared imports make the
license boundary difficult to audit. Until then, a split creates coordination
cost without adding a reliable legal boundary.

## Approval Checklist

- Confirm copyright ownership and contributor/relicensing authority for code already released under MIT.
- Obtain legal review of AGPL applicability to the hosted product, APIs, frontend assets, and proprietary boundary.
- Decide whether prior MIT releases remain supported and identify the first release under any new terms.
- Define directory-level license notices, asset exclusions, contribution terms, and third-party attribution handling.
- Only after approval, replace or supplement license files and update product and repository notices.

## Decision Record

| Decision | Outcome |
| --- | --- |
| AGPL vs BSL evaluation | Recommend AGPL for future open-core code, pending approval |
| Monorepo vs split evaluation | Recommend monorepo with explicit boundaries initially |
| Legal/owner approval | Pending; no relicensing performed |
