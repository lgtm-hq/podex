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

Every path that writes this content again therefore republished it. These paths
include:

- derivative generation, chunking and summaries;
- graph triples and media relations with a provenance episode;
- transcript cleaned text;
- any new `Mention` for the same episode and media;
- media merges and splits that move mentions between records.

Media summaries quote mention context from many episodes, alert counts and
digests count mentions, and catalog stats are cached. None of the derivative
paths are wired to a production trigger yet. The search projection, its repairs,
the derivative worker and review approval are being built on them, and a rebuild
must not be a way to undo a takedown.

Removal is a legal obligation. It must not depend on retention, retrieval or
purge settings, which are optional features that are off by default.

## Decision

An approved takedown records one `content_suppressions` row per suppression
scope it requested, in the same transaction and before any destructive step:

- `unpublish_mentions` becomes scope `mentions`; `suppress_derivatives` becomes
  scope `derivatives`.
- The subject is a podcast, an episode, or, for a mention takedown, the
  `(episode, media)` pair of the deleted mention. A mention takedown's
  derivative scope covers its whole episode, as execution already does. A
  podcast suppression covers every episode of the podcast, including episodes
  discovered after the approval.
- Raw retention actions (`suppress_raw_transcript`, `register_source_opt_out`)
  keep their storage and opt-out semantics and record no suppression. So a
  purged raw transcript can be re-acquired and stored again under the existing
  retention and opt-out policy. Publication guarantees do not cover raw storage.
- With either scope, execution also deletes the media summaries of every media
  record mentioned in the covered content, since they may quote it. The next
  summary is built without it.
- A non-null `suppression_key` (subject type, ids, scope) is unique, because a
  unique constraint over nullable id columns does not deduplicate. A media merge
  moves pair suppressions to the surviving record.

One predicate, `suppressed_clause` in `podex.models.content_suppression`,
defines what a suppression covers. It reads the episode's podcast through its
own alias, so it answers for the outer row whether it is given a literal id, a
`Mention` column or an `Episode` column. Everything else uses it:

- **Reads.** Every read of `Mention` rows that reaches a user goes through
  `published_mentions` or `count_published_mentions`
  (`podex.services.content_suppression`). This covers public lists and counts,
  stats, account alert evaluation, merge alert baselines and digest delivery.
  Digest delivery also trims pending events to activity that is still published.
  Media summaries read through `summary_source_mentions`, which also leaves out
  derivative-suppressed episodes.
- **Enforced reads.** `tests/test_public_read_suppression.py` calls every public
  `GET` route in the OpenAPI schema, every signed-in `/me` route, alert
  evaluation and the digest email. It fails if any statement reads `mentions`
  without the predicate.
- **Writes.** Session events in `podex.models.suppression_guard`, installed by
  importing `podex.models`, check every ORM flush that writes a guarded row
  (`GUARDED_MODELS`, one rule per model) and raise `ContentSuppressedError`.
  Covered writes are inserts, content changes and moves between episodes or
  media records, checked at the destination and, for moves, at the origin.
  Removing content, detaching provenance and repointing media identity are
  allowed. A test fails when a table gains an `episodes` foreign key without
  being guarded or listed as exempt with a reason.
- **Provider calls.** Writers that send episode text to a provider authorise
  first: `generate_episode_derivatives`, `sync_semantic_chunks_for_transcript`,
  `generate_episode_summary` and `generate_media_summary`. Authorising takes the
  takedown-excluding lock before the text is read and checks under it.
- **Enforcement scope.** The guarantee holds for ORM-flushed writes and the
  reads above. Core `insert`/`update`/`delete`, `Query.update`/`delete`, raw SQL
  and the `bulk_*` session methods skip the flush guard. They are forbidden for
  guarded tables: `tests/test_guarded_table_writes.py` scans `backend/src`,
  `backend/scripts` and `backend/alembic` and fails on any such write.
  Exemptions are listed with a reason; today these are takedown execution (which
  only removes content) and migrations. A database-level constraint or trigger
  is a possible later hardening.

Lock order, the one the media merge already uses: media rows before podcast
rows. All locks are held until the transaction ends.

- **Takedown.** It locks the media records whose summaries quote the covered
  text `FOR KEY SHARE`, then the covered podcast rows `FOR UPDATE`, then reads
  what to delete. If covered mentions changed between those two steps, it
  refuses and is retried rather than take a media lock out of order.
- **Guarded flush.** Before writing, it locks the media rows of guarded mentions
  `FOR KEY SHARE`, then the podcast rows of every touched episode `FOR SHARE`,
  ascending id. It checks the predicate after writing.
- **Authorised provider call.** It locks the episodes' podcast rows `FOR SHARE`
  (a media summary locks its media row first) and then checks.
- **Media merge.** It locks both media records `FOR UPDATE`, unchanged.

On PostgreSQL a takedown and a writer of the same podcast therefore run one
after the other. A takedown that commits first refuses the writer before any
text is read. A writer or provider call that started first finishes, and the
takedown then deletes what it wrote. SQLite ignores row locks and has one
writer. There the guard checks after its write, when it holds the database write
lock. Authorisation takes that lock explicitly with a no-op write, so a takedown
cannot commit while text is with a provider.

Not decided here: whether a podcast or episode takedown also hides the podcast
or episode page and its list entries. The page stays visible; adding that needs
a new scope and an owner decision.

## Consequences

- **Rebuilds.** Rebuild paths (search projection, repairs, the derivative
  worker, review approval) inherit the check. A batch rebuild can skip
  suppressed episodes up front with `suppressed_episode_ids`.
- **Lifting a suppression.** The application cannot lift one. That needs an
  operator action with its own audit trail, which does not exist yet.
- **Takedown wait during provider calls.** While a writer holds episode text for
  a provider, a takedown of that podcast waits until the writer's transaction
  ends. The worst-case wait is the sum of that transaction's provider call
  timeouts plus its own database work.
  - `SummaryProvider` and `EmbeddingProvider` have no implementation yet; each
    must bound its call with a timeout.
  - The Anthropic extraction and cleanup adapters rely on the SDK default
    timeout (600 s per read, two retries). They are not called under this lock
    today. Wiring them under it would make that default the takedown wait unless
    a lower timeout is set.
  - On SQLite the takedown fails with `database is locked` after its busy
    timeout and is retried.
- **Moving hidden content.** A mention covered by a suppression cannot be moved
  to another media record or episode. Media splits and merges that would move
  one are refused, and the operator resolves the hidden row first.
- **Caches.** The stats cache stores the suppression version (row count and
  highest id) with its value. The first read after a takedown commits recomputes
  it in every process. No other cache holds mention content or counts.
- **Cost.** Each guarded flush runs at most three lookup statements (media
  locks, podcast locks, one check per scope) per 5,000 distinct episodes or
  pairs, independent of how many rows it writes. Every public mention read adds
  one indexed `EXISTS`. The suppression table is expected to stay small.
- **Retention boundaries.** Purge and takedown serialisation of transcript
  objects and its lock remain with the retention work. When added, transcript
  locks come after the podcast lock.
- **Migration.** Revision `0022` backfills suppressions from takedowns that
  already ran:
  - a mention case resolves only from the execution result its decision audit
    row recorded;
  - cases without unambiguous evidence are logged by id and must be recorded by
    hand before a rebuild path is enabled;
  - offline (`--sql`) output creates the table without the backfill.
