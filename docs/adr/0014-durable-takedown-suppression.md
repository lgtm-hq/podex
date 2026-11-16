<!-- markdownlint-disable MD041 -->

# 0014. Approved takedowns leave a durable suppression checked on every write

- Status: proposed
- Date: 2026-11-16
- Tags: takedowns, data, legal

## Context

Executing an approved takedown deletes the rows that exist when it runs:
mentions, semantic chunks, episode summaries and derivative runs, and the
cleaned and digest transcript text. Nothing recorded what had been removed in a
form a writer could look up. The only durable trace was the `TakedownRequest`
case row, which is a case record and is not keyed by episode or media.

Every path that writes this content again therefore republished it: derivative
generation, chunking, summaries, graph triples and media relations with a
provenance episode, transcript cleaned text, any new `Mention` for the same
episode and media, and a media merge that moves a mention onto a taken-down
pair. None of these paths are wired to a production trigger yet, but the search
projection, its repairs, the derivative worker and review approval are being
built on them. A rebuild must not be a way to undo a takedown.

Removal is a legal obligation. It must not depend on retention, retrieval or
purge settings, which are optional features that are off by default.

## Decision

An approved takedown records one `content_suppressions` row per suppression
scope it requested, in the same transaction and before it deletes anything:

- `unpublish_mentions` becomes scope `mentions`; `suppress_derivatives` becomes
  scope `derivatives`.
- The subject is a podcast, an episode, or, for a mention takedown, the
  `(episode, media)` pair of the deleted mention. A mention takedown's
  derivative scope covers its whole episode, as execution already does. A
  podcast suppression covers every episode of the podcast, including episodes
  discovered after the approval.
- Raw retention actions (`suppress_raw_transcript`, `register_source_opt_out`)
  keep their storage and opt-out semantics and record no suppression.
- A non-null `suppression_key` (subject type, ids, scope) is unique, because a
  unique constraint over nullable id columns does not deduplicate. A media merge
  moves pair suppressions to the surviving record.

One predicate, `suppressed_clause` in `podex.models.content_suppression`,
defines what a suppression covers. Everything else uses it:

- Every public read of `Mention` rows goes through `published_mentions` or
  `count_published_mentions` (`podex.services.content_suppression`).
  `tests/test_public_read_suppression.py` calls every public `GET` route in the
  OpenAPI schema and fails if any statement reads `mentions` without the
  predicate, so a new route or query cannot forget it.
- Session events in `podex.models.suppression_guard`, installed by importing
  `podex.models`, check every ORM flush that inserts or changes a guarded row
  (`GUARDED_MODELS`) and raise `ContentSuppressedError`. A writer cannot skip
  the check by calling a lower-level function. A test fails when a table gains
  an `episodes` foreign key without being guarded or listed as exempt with a
  reason.
- Writers that send episode text to a provider (`generate_episode_derivatives`,
  `sync_semantic_chunks_for_transcript`, `generate_episode_summary`) check
  first, so suppressed text never leaves the system.

Concurrency: a takedown locks the podcast rows it covers `FOR UPDATE` before it
reads what to delete. A guarded flush locks the podcast rows of the episodes it
touches `FOR SHARE`, ascending id, before it writes, and checks the predicate
after it writes. On PostgreSQL the two serialize per podcast; on SQLite the
writer holds the database write lock by the time it checks. Either way the
takedown wins: a writer that wrote first is deleted by the takedown that follows
it, and a writer that comes second is refused.

Not decided here: whether a podcast or episode takedown also hides the podcast
or episode page and its list entries. The page stays visible; adding that needs
a new scope and an owner decision.

## Consequences

- Rebuild paths (search projection, repairs, the derivative worker, review
  approval) inherit the check. A batch rebuild can skip suppressed episodes up
  front with `suppressed_episode_ids`.
- Lifting a suppression is not possible through the application. It needs an
  operator action with its own audit trail, which does not exist yet.
- Bulk Core `INSERT` or `UPDATE` statements bypass the flush guard. They are not
  used for guarded tables today; the public read predicate still hides a mention
  written that way. Guarded tables are written through the ORM.
- Lock order for writers of episode content: media rows (when that ordering is
  introduced for media writers), then the podcast suppression lock, then any
  transcript lock. Takedown execution takes no media lock today.
- Every guarded flush and public mention read runs one extra indexed `EXISTS`
  query. The suppression table is expected to stay small.
- Revision `0022` backfills suppressions from takedowns that already ran. A
  mention case resolves only from the execution result its decision audit row
  recorded; cases without unambiguous evidence are logged by id and must be
  recorded by hand before a rebuild path is enabled. Offline (`--sql`) output
  creates the table without the backfill.
