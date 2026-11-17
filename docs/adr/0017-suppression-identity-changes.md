<!-- markdownlint-disable MD041 -->

# 0017. Suppressions survive episode moves and subject deletes

- Status: proposed
- Date: 2026-11-17
- Tags: takedowns, data, legal

## Context

[ADR 0015](./0015-durable-takedown-suppression.md) records a takedown as
`content_suppressions` rows keyed by row ids: a podcast id, an episode id, or
an `(episode, media)` pair. `suppressed_clause` reads a podcast suppression
through the episode's current `podcast_id`. Two row changes could therefore
lift a suppression without anyone deciding to:

- **Moving an episode.** After a podcast takedown, setting the episode's
  `podcast_id` to another podcast took it out of the suppression. `Episode` is
  not a guarded model, so the move committed, and new mentions and derivatives
  of the episode were then accepted and published.
- **Deleting a subject.** `content_suppressions.podcast_id` and `episode_id`
  were `ON DELETE CASCADE`. Deleting a suppressed episode or podcast deleted
  its suppressions. Content ingested again got a new row (on SQLite possibly
  the same id) with nothing to refuse it.

No production code moves an episode or deletes an episode or podcast today
(podcasts are archived). The first feed reassignment, duplicate-podcast merge
or hard deletion would open these gaps.

## Decision

This decision extends ADR 0015. It adds one lock step and changes one detail
of the guard: podcast locks and passed checks are remembered for one flush,
not for the whole transaction (see "Lock order").

**An episode that leaves a podcast takes the podcast's suppressions with it.**
The flush guard (`podex.models.suppression_identity`) finds an `Episode`
whose podcast is assigned by column, relationship or collection. It does not
trust the session's copy of the episode: another transaction may have moved
the episode, or taken its podcast down, since the copy was loaded.

- **Origin from the database.** Before reading anything, the guard locks the
  episode's row and reads its persisted `podcast_id` under that lock. The
  episode moves only if that podcast differs from the one it is assigned to.
- **Carry.** For each podcast suppression of the podcast the episode really
  leaves, it adds an episode suppression in the same flush: same scope, same
  takedown case, key `episode:<id>:<scope>`. A key that is already recorded
  is not added again.
  - Suppressions pending in the session count. A takedown records one scope
    per flush, and an unflushed move can be written by the first of those
    flushes.
  - An episode that left a podcast earlier in the transaction also gets any
    suppression of that podcast recorded later in it. So a takedown that
    counted the episode before the move was written covers it with every
    scope it reports. This can over-suppress, which is the safe side.
- **Moving into a suppressed podcast** stays allowed. The podcast suppression
  covers the episode through `suppressed_clause`, so nothing is copied.
- **Change counter.** A move into or out of a podcast with a podcast
  suppression changes what is published: cached stats counted the episode's
  mentions as published or hidden. Such a move bumps the change counter in the
  same transaction, at commit, after pending writes are flushed. The counter
  stays the last lock taken. A flush that moves nothing reads nothing for it.
- **Carried rows** are suppressions like any other. Moving the episode back
  does not remove them, and the application cannot lift them (ADR 0015).

**Deleting a podcast or episode that a suppression covers is refused.**

- **Through the ORM.** Before the flush writes, the guard refuses to delete:
  - an `Episode` covered by its own suppression, a pair suppression of it, or
    its podcast's suppression;
  - a `Podcast` covered by its own suppression or any of its episodes'.

  It raises `ContentSuppressedError` naming the row, such as `Podcast 3
  (deleted while a takedown suppression covers it)`. The error now takes an
  optional episode id and a podcast id, for a podcast with no covered episode.
- **In the database.** Revision `0026` makes both foreign keys of
  `content_suppressions` `ON DELETE RESTRICT`. The database therefore refuses
  to delete a row a suppression names directly: a suppressed podcast, a
  suppressed episode, the episode of a pair suppression, and the episodes a
  podcast delete would cascade to when one of them is named.
- **Not in the database.** An episode covered only by its podcast's
  suppression is not referenced by any suppression row. A Core or raw
  `DELETE` of it succeeds in the database, and a new episode may then reuse
  its id. Only the ORM guard and the structural scan stop that. A database
  trigger is a possible later hardening; this decision does not add one.

**Inserts can move an episode too.** An upsert (`INSERT ... ON CONFLICT DO
UPDATE`, `INSERT OR REPLACE`, `REPLACE`) can change an existing episode's
podcast, and an insert can give a deleted row's id to new content. The ORM
does neither on its own: an ORM `merge` of an existing episode is flushed as
an update, which the guard handles. The structural scan refuses Core and raw
inserts, upserts and replaces of `Episode` and `Podcast`.

**No stable external identity.** A suppression does not record a feed URL or
an episode GUID, so it cannot recognise content that is ingested again under
a new row. Deleting a suppressed row and ingesting it again is refused at the
delete, not tracked afterwards. A real deletion of suppressed content needs
an operator action this decision does not build:

- either a stable identity carried on the suppression (for example feed URL
  and episode GUID, checked at ingestion) so re-ingested content stays
  refused after the row is gone;
- or an audited lift of the suppression, with its own decision on whether the
  content may return. ADR 0015 already notes that this action does not exist.

Until then, archiving is how a suppressed podcast is retired.

**Lock order.** ADR 0015's order gains one step, between media and podcast
rows:

1. media rows of guarded mentions, `FOR KEY SHARE`;
2. rows of the episodes the flush moves, `FOR NO KEY UPDATE`, ascending id,
   reading their persisted podcast. On SQLite, which ignores row locks, the
   database write lock is taken first, with the same no-op write that
   authorisation uses;
3. podcast rows `FOR SHARE`: those of guarded rows' episodes, of deleted
   episodes, and the podcast each moved episode really leaves (as step 2 read
   it, even if the flush had locked another podcast for the episode);
4. the change counter, last, at commit.

`FOR NO KEY UPDATE`, rather than `FOR UPDATE`, is the lock the move's own
`UPDATE` takes anyway; it is taken earlier, before the origin is read. Two
moves of one episode therefore run one after the other. `FOR UPDATE` would
deadlock with a takedown:

- the mover would hold the episode and wait for the podcast's `FOR SHARE`;
- a takedown holding that podcast `FOR UPDATE` would then wait for the
  `FOR KEY SHARE` that recording an episode or pair suppression takes on the
  episode.

Why the order cannot deadlock with a takedown or a guarded flush:

- **Episode rows.** Takedowns and guarded flushes lock episode rows only
  `FOR KEY SHARE`, through foreign key checks when they insert a row that
  references the episode. That never conflicts with `FOR NO KEY UPDATE`, so
  neither ever waits for a mover's episode lock.
- **Podcast rows.** A mover waits only for a podcast held `FOR UPDATE` (a
  takedown) or updated (an operator edit). Each of those holds a single
  podcast row and, as above, never waits for an episode row. So no cycle
  closes.
- **Media rows and counter.** A mover's media locks are `FOR KEY SHARE`, as
  a takedown's are. The counter is taken only at commit, after every other
  lock.
- **Movers among themselves.** Two transactions that move the same episodes
  in opposite orders, across separate flushes, can deadlock exactly as two
  plain `UPDATE`s would. PostgreSQL then aborts one with a retryable error.

**Remembered locks and checks.** A podcast lock and a passed check are kept
until the flush ends, no longer, because another transaction can move an
episode between two flushes of this one. The next flush locks and checks the
podcast the episode is in then. A moved episode is also dropped right after
the move, so the check after the write locks the podcast it joined. Media
locks are still kept until the transaction or savepoint ends. A media record
cannot change identity.

**Structural scan.** `tests/test_guarded_table_writes.py` treats `Episode` and
`Podcast` like the guarded tables, for inserts, upserts, replaces, updates
and deletes that skip the flush.

- **Forms it reads.** It reads a statement's target whether it is passed
  positionally or as `table=`, through `aliased(...)` or `__table__`, or as a
  table named by string. Raw SQL targets are read after comments are
  removed, quoted or schema-qualified.
- **Exemptions.** Each exemption now names the exact verbs and tables its
  reason covers. Takedown execution keeps its bulk deletes of derivative runs,
  semantic chunks and episode summaries, and its bulk update of transcripts.
  The migrations exemption is removed. Its reason (schema changes and the
  suppression backfill) covers no table the scan reads, and no migration
  writes one.

## Consequences

- **Cost.** A flush that moves and deletes nothing runs no added statement.
  Moves and deletes are found in memory.
  - **Moves.** A moving flush adds:
    - one episode-lock statement per 5,000 moved episodes;
    - one statement per 5,000 origin and destination podcasts (their
      suppressions);
    - when there is something to carry, one per 5,000 keys (those already
      recorded).

    On SQLite it also runs the no-op write. The moved episodes join the
    podcast-lock statement; when the flush writes no guarded row, that
    statement is added too. A move that changes coverage adds one counter
    read and update at commit.
  - **Deletes.** The flush adds one statement per 5,000 deleted episodes and
    one per 5,000 deleted podcasts. The deleted episodes join the
    podcast-lock statement.
  - **Guarded rows for a moved episode.** When one is written in the same
    flush, the check after the write locks the episode's new podcast: one
    more statement.
  - **ADR 0015's bound** for guarded rows is unchanged: at most five lookups
    per 5,000 episodes or pairs in each flush. A transaction with several
    flushes now runs the podcast lock and check again in each.
- **Waits.** A move waits for another move or update of the same episode, and
  for a takedown or operator edit of the podcast it leaves. Nothing waits for
  a move except those.
- **Residual race.** Within one flush, the episode can be moved between the
  guard's podcast lock and its check after the write. If a takedown of the
  new podcast commits in that window, the row is written but hidden by every
  read; it is not published. Making a writer's lock block movers would let a
  mover deadlock with a writer that spans several flushes.
- **Writes around the flush.** The database refuses deletes of rows a
  suppression names; everything else around the flush is caught only by the
  structural scan: a move by Core or raw `UPDATE`, an upsert, a delete of an
  episode covered only by its podcast.
- **Unaffected paths.** Account deletion, and media merges and splits, neither
  move nor delete episodes or podcasts.
- **Deletion is blocked.** Any future hard-delete or podcast-merge feature
  must handle `ContentSuppressedError` for suppressed rows. A merge can move
  the episodes first, which carries their suppressions, and then delete the
  emptied podcast unless it has its own suppression.
- **Rollout.** Apply revision `0026` before or with the code. Nothing in
  production moves or deletes these rows, so the order is not critical.
  - Existing suppressions are kept unchanged. Only the delete rule of the two
    foreign keys changes.
  - On PostgreSQL the constraints are dropped and added again under their
    default names. Adding them briefly locks `content_suppressions`,
    `podcasts` and `episodes` and checks the existing suppression rows.
  - On SQLite the table is copied with its rows, check constraint and indexes.
- **Rollback.** Revert the change and downgrade to `0025`, which restores
  `ON DELETE CASCADE` and keeps every row. Episode suppressions carried in the
  meantime stay: they are valid suppressions of the same takedown cases.
