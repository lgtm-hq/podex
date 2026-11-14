<!-- markdownlint-disable MD041 -->

# 0009. Persist allowlisted ingestion failure descriptors

- Status: proposed
- Date: 2026-11-14
- Tags: ingestion, security, observability

## Context

Discovery records provider failures in result lists, ingestion runs and
scheduled work items. Ops callers read those same summaries. Extraction and
transcript cleanup also return failure lists. Exception messages and generated
response snippets can contain credentials, row values or provider content. The
logging boundary described in ADR 0008 cannot repair strings already persisted
or formatted before logging.

## Decision

New failure diagnostics contain only a code-owned source or stage, runtime
exception class matching `[A-Za-z_][A-Za-z_0-9]{0,63}` (otherwise `Exception`),
a validated HTTP status or the existing database descriptor (class and SQLSTATE
or SQLite error name), and a fixed reason code. They never read exception
messages, URLs, headers, parameters or response bodies. Reuse the existing
database descriptor and bounded exception-tree helpers. Before storing runner
results, accept only the rendered descriptor grammar; replace other text with
`discovery: unclassified_failure`. Each descriptor is at most 384 characters.
The runner-exception path uses the same validation. Keep the existing string and
null API shape and document the new meaning in OpenAPI.

Pass exception objects to logging. Database-only diagnostic trees go through the
existing sanitising boundary. Other failures use its existing stand-in helper to
retain class and traceback with the allowlisted descriptor as the message; omit
unsafe messages, notes and chains. Arbitrary HTTP error text has no global log
scrubber, so retaining its full message would violate this boundary. External
exceptions are neither mutated nor re-raised. Feed fetching supplies a closed
`FailureReason` enum at every error boundary, plus the HTTP status when present.
This preserves DNS, deadline, size, redirect, encoding and HTTP failure kinds
without inspecting fixed or arbitrary messages. Provider logs also retain fixed
operation names, caller-supplied identifiers and page numbers. Embedding
failures use the same persistence boundary, including fixed validation-shape
reasons. Retry policy, recorded failure cases and scheduler transaction
boundaries stay unchanged.

Leave historical rows unchanged. Their original failure kinds cannot reliably be
reconstructed from free text, and a destructive rewrite would erase diagnostic
evidence. This fix prevents new unstructured diagnostics; it does not
retroactively remove previously stored content. The query layer validates every
stored run summary and work-item error. Valid descriptors (including fully
validated joined summaries), null and the known fixed historical reasons are
returned unchanged; all other values become `legacy failure text withheld`. The
fixed historical reasons are `interrupted before completion`,
`Missing podcast_id`, `Podcast not found`, `Podcast is not active` and
`Discovery schedule disabled`. Read validation also bounds a joined summary to
4,096 characters. The schema documents this guard; the stored historical rows
are untouched.

## Consequences

Operators can distinguish new failures by source, class and protocol code, and
use logged tracebacks to locate their origin. HTTP bodies, arbitrary exception
messages and generated response snippets are unavailable as failure diagnostics.
Custom discovery runners must return descriptors or receive the fixed fallback.
Changing this storage contract requires updating this decision and its tests.
There is no migration, and rollback is a code revert.
