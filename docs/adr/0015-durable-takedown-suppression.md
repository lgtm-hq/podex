<!-- markdownlint-disable MD041 -->

# 0015. Takedowns leave a durable suppression checked on every write

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
  `(episode, media)` pair of the named mention. A mention takedown's derivative
  scope covers its whole episode. A podcast suppression covers every episode of
  the podcast, including episodes discovered after the approval.
- Raw retention actions (`suppress_raw_transcript`, `register_source_opt_out`)
  keep their storage and opt-out semantics and record no suppression. So a
  purged raw transcript can be re-acquired and stored again under the existing
  retention and opt-out policy. Publication guarantees do not cover raw storage.
- A non-null `suppression_key` (subject type, ids, scope) is unique, because a
  unique constraint over nullable id columns does not deduplicate.
- A `content_suppression_versions` counter increases by one in every
  suppression-relevant transaction: takedown execution, media merges and splits,
  and the backfill. Unlike a count or highest id of suppression rows, it never
  repeats. Its single row is created with the table (revision `0022`, or the
  models' `after_create` hook), so a bump only updates it under a row lock and
  two first bumps queue instead of racing to insert it.

What execution removes, beyond what it removed before this decision:

- **Every mention of a suppressed pair.** Suppression is per pair, so a mention
  takedown deletes every mention of that media in that episode, not only the one
  named. The case's `metadata_json` records how many extra rows it removed
  (`pair_mentions_removed`).
- **Media summaries that may quote the covered text.**
  - A mention-only takedown covers the mention's media record. Any other
    suppression covers every media record mentioned in the covered episodes.
  - A summary is kept only when its stored provenance (`source` =
    `media_metadata_mentions`, `mention_context_count` = 0) proves it quoted no
    mention context; without that proof it is dropped.
  - Rows are deleted through the session, and `metadata_json` records
    `media_summaries_dropped`.
- **Deleted mention ids** leave every undelivered alert event in the same
  transaction, so a later mention that reuses an id (SQLite can) is never
  reported as one of them.

Media identity changes carry suppressions:

- **Merge.** Pair suppressions move to the surviving record. Before alert
  baselines are read, mentions of either record that a suppression of either
  record covers in that episode are deleted the same way, instead of refusing
  the merge.
- **Split.** The new record gets a copy of every pair suppression of the record
  it was split from, keeping each copy's takedown case.

One predicate, `suppressed_clause` in `podex.models.content_suppression`,
defines what a suppression covers. It reads the episode's podcast through its
own alias, so it answers for the outer row whether it is given a literal id, a
`Mention` column or an `Episode` column. Everything else uses it:

- **Reads.** Every read of `Mention` rows that reaches a person goes through
  `published_mentions`, `count_published_mentions` or `published_mention_clause`
  (`podex.services.content_suppression`). This covers:
  - public lists and counts, and stats;
  - ops podcast and media counts;
  - account alert evaluation, merge alert baselines and digest delivery;
  - the retention coverage check.

  A media alert event records the ids of the mentions it reports (revision
  `0023`). Delivery counts how many of those are still published, and an event
  with none left is held back, never deleted. Media summaries read through
  `summary_source_mentions`, which also leaves out derivative-suppressed
  episodes.

- **Writes.** Session events in `podex.models.suppression_guard`, installed by
  importing `podex.models`, check every ORM flush that writes a guarded row
  (`GUARDED_MODELS`, one rule per model) and raise `ContentSuppressedError`,
  which names the refused row and why.
  - Inserts, content changes and moves between episodes or media records are
    checked at the destination and, for moves, at the origin. The columns an
    origin check reads have active history, so their old value is loaded even
    when the row was expired by a commit.
  - Removing content, detaching provenance and repointing media identity are
    allowed.
- **Provider calls.** Writers that send episode text to a provider authorise
  first: `generate_episode_derivatives`, `sync_semantic_chunks_for_transcript`,
  `generate_episode_summary` and `generate_media_summary`.
  - Authorising checks without a lock, takes the lock, then checks again. A
    refusal releases the lock it took (a savepoint on PostgreSQL; on SQLite, the
    write transaction it began).
  - A media summary re-reads the episodes that mention the record after locking
    their podcasts, until the set is stable (three attempts), or refuses with a
    retryable error.

Enforcement scope. The guarantee holds for ORM-flushed writes and the reads
above. Writes that skip the flush are forbidden for guarded tables:
`tests/test_guarded_table_writes.py` parses `backend/src`, `backend/scripts` and
`backend/alembic` with `ast` and fails on any such write. That covers Core
`insert`/`update`/`delete` including dialect variants, `Query.update`/`delete`,
raw SQL and the `bulk_*` methods, and the parse resolves import (including
relative), model and table-name aliases.

- **Exemptions.** They are listed with a reason: takedown execution (it only
  removes content) and migrations.
- **What the structural tests cannot see.**
  - The bypass scan misses writes built dynamically (a model or SQL chosen at
    run time) and helpers outside these directories.
  - The read tests (`tests/test_public_read_suppression.py`, covering public,
    signed-in and ops routes, alert evaluation and the digest email) see only
    the statements those calls run. A read reached only through a parameter
    combination the tests do not use is not seen.
  - The table classification follows foreign keys transitively to `episodes`,
    and misses content linked to an episode without a foreign key.
- **Database-level enforcement.** A constraint or trigger is a possible later
  hardening.

Lock order, the one the media merge already uses: media rows before podcast
rows. All locks are held until the transaction ends.

- **Takedown.** It bounds its lock waits, then takes its locks in this order and
  reads what to delete:
  - the bound is `lock_timeout` 5 s on PostgreSQL, and the connection busy
    timeout (5 s by default) on SQLite;
  - the media records it covers, `FOR KEY SHARE`;
  - the covered podcast rows, `FOR UPDATE`.

  If covered mentions changed between those steps, or a wait expires, the
  decision answers a retryable 409 and nothing is changed.

- **Guarded flush.** Before writing, it locks the media rows of guarded mentions
  `FOR KEY SHARE`, then the podcast rows of every touched episode `FOR SHARE`,
  ascending id. It checks after writing.
- **Authorised provider call.** Derivative generation locks the episode's media
  rows when it will summarise them; then the episodes' podcast rows are locked
  `FOR SHARE`.
- **Change counter.** The `content_suppression_versions` row is always the last
  lock taken.
- **Media merge.** It locks both media records `FOR UPDATE`, unchanged.

On PostgreSQL a takedown and a writer of the same podcast therefore run one
after the other. A takedown that commits first refuses the writer before any
text is read. A writer or provider call that started first finishes, and the
takedown then deletes what it wrote. SQLite ignores row locks and has one
writer. There the guard checks after its write, when it holds the database write
lock, and authorisation takes that lock explicitly with a no-op write.

Not decided here: whether a podcast or episode takedown also hides the podcast
or episode page and its list entries. The page stays visible; adding that needs
a new scope and an owner decision.

## Consequences

- **Rebuilds.** Rebuild paths (search projection, repairs, the derivative
  worker, review approval) inherit the check. A batch rebuild can skip
  suppressed episodes up front with `suppressed_episode_ids`.
- **Lifting a suppression.** The application cannot lift one. That needs an
  operator action with its own audit trail, which does not exist yet.
- **Waits during provider calls.** While a writer holds episode text for a
  provider, its `FOR SHARE` podcast lock lasts until its transaction ends.
  - **Takedowns.** A takedown of that podcast waits up to 5 seconds, then
    answers a retryable 409.
  - **Operator edits.** Operator edits and archiving of that podcast (row
    updates) wait for as long as the writer's transaction lasts. That is at most
    the sum of its provider call timeouts plus its own database work.
  - **Provider timeouts.** `SummaryProvider` and `EmbeddingProvider` have no
    implementation yet; each must bound its call. The Anthropic extraction and
    cleanup adapters use the SDK default (600 s per read, two retries). They are
    not called under this lock today, and would need a lower timeout before
    being wired under it.
  - **SQLite.** The same writer holds the database write lock, so any other
    write waits up to its busy timeout.
- **Moving hidden content.** Merges no longer refuse because of a suppressed
  pair; they delete what the carried suppression covers. A hidden row can only
  exist when written around the guard. A split or episode move of such a row is
  refused with an error naming the row (`Mention <id>`); it can be removed by
  executing a mention takedown of it, which deletes its pair.
- **Caches.** The stats cache stores the change counter with its value, and the
  first read after any suppression-relevant commit recomputes it in every
  process. No other cache holds mention content or counts.
- **Cost.**
  - Each guarded flush runs at most five lookup statements per 5,000 distinct
    episodes or pairs, independent of how many rows it writes: media locks,
    podcast locks, one derivatives episode check, one mentions episode check
    (only for mentions whose media is unknown) and one pair check. A flush of
    mentions takes three; mentions mixed with derivative rows take four.
  - Every user-facing mention read adds one indexed `EXISTS`.
  - Each digest run re-counts every held-back media event with one query.
- **Graph evidence.** Execution does not remove graph triples or media relations
  built from a suppressed episode. They keep their evidence text and provenance
  episode, as before. No route, summary source or provider reads them today; the
  ops media detail lists relations without evidence and has no route. The guard
  refuses new evidence and moving existing evidence off the episode. The search
  projection must exclude them before it reads graph data.
- **Alert events raised before revision `0023`.** These events have no recorded
  ids and are delivered with their stored counts, including a mention taken down
  after they were raised, as before. They cannot be resolved correctly
  afterwards, because their own mentions are unknown.
- **Alert counting nets removals.** A new-mention alert compares the published
  count with its baseline, as before. Mentions a takedown deletes therefore
  offset mentions added in the same window. Deleting the whole pair can offset
  more: with two new mentions and a takedown of a pair holding two rows, `main`
  reports one new mention and this change reports none. Reporting new mentions
  by id instead of by count is outside this decision.
- **Retention boundaries.** Purge and takedown serialisation of transcript
  objects and its lock remain with the retention work. When added, transcript
  locks come after the podcast lock.
- **Rollout.** Revision `0022` creates the tables and runs the backfill. The
  backfill is also `python -m podex.suppression_backfill [--dry-run]`. It is
  idempotent: it writes only missing rows, and reports unresolved cases by id
  without guessing. To deploy:
  1. Migrate first.
  2. Hold takedown approvals until every instance runs the new code; an instance
     on the previous code executes a takedown without recording its suppression.
  3. Run the backfill once more.

  New code on the old schema fails closed: reads that need the suppression table
  answer 500 rather than serving unsuppressed content. Offline (`--sql`) output
  creates the tables only; the backfill then runs as the command. A migration
  that failed part way can be retried, because tables and indexes are created
  only when missing.
