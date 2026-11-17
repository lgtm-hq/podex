<!-- markdownlint-disable MD041 -->

# 0016. Suppressions survive episode moves and subject deletes

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

This decision extends ADR 0015 and changes none of its rules.

**An episode that leaves a podcast takes the podcast's suppressions with it.**
The flush guard detects an `Episode` whose podcast changes by column,
relationship or collection assignment. For each podcast suppression of the
podcast the episode leaves, it adds an episode suppression in the same flush:
same scope, same takedown case, key `episode:<id>:<scope>`. A key that is
already recorded is not added again.

- Moving an episode into a suppressed podcast stays allowed. The podcast
  suppression already covers it through `suppressed_clause`, so nothing is
  copied. Checks the transaction remembered are dropped after any move, so an
  earlier passing check is not reused for the episode's new podcast.
- Carried rows are suppressions like any other. Moving the episode back does
  not remove them, and the application cannot lift them (ADR 0015).
- The change counter is not bumped. The episode is covered before and after
  the move, so nothing visible changes.

**Deleting a podcast or episode that a suppression covers is refused.**

- Before the flush writes, the guard refuses to delete an `Episode` covered
  by its own suppression, a pair suppression of it, or its podcast's
  suppression. It refuses to delete a `Podcast` covered by its own suppression
  or any of its episodes' suppressions. It raises `ContentSuppressedError`
  naming the row, such as `Podcast 3 (deleted while a takedown suppression
  covers it)`. The error now takes an optional episode and a podcast id, for
  a podcast with no covered episode.
- Revision `0025` makes both foreign keys `ON DELETE RESTRICT`. A delete that
  skips the flush therefore fails in the database, including the
  `episodes.podcast_id` cascade of a podcast delete.

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

**Lock order** is the order ADR 0015 defines: media rows first, then podcast
rows `FOR SHARE`, with the counter last.

- Moved and deleted episodes join the guard's podcast-lock statement. For a
  moved episode that statement locks the podcast it leaves, because the
  database row still names it.
- A takedown of that podcast locks it `FOR UPDATE`. So the takedown either
  commits first, and the move then reads and carries its suppressions, or
  starts after the move, when the episode is no longer part of the podcast.
- On SQLite, which ignores row locks, the move takes the database write lock
  with the same no-op write that authorisation uses before it reads what to
  carry.
- Once moved, the episode is dropped from the transaction's lock cache, so
  the next guarded write for it locks the podcast it joined.

**Structural scan.** `tests/test_guarded_table_writes.py` now also fails on an
`Episode` update and on an `Episode` or `Podcast` delete that skips the flush:
Core statements, `Query.update`/`delete`, raw SQL, and tables named by string.
Inserts of either cannot lift a suppression and are not reported, and neither
are `Podcast` updates.

## Consequences

- **Cost.** A flush that moves and deletes nothing runs no added statement.
  Moves and deletes are found in memory.
  - With moves, the flush adds one statement per 5,000 origin podcasts
    (their suppressions) and, when there is something to carry, one per 5,000
    keys (those already recorded). On SQLite it also runs the no-op write.
    The moved episodes join the podcast-lock statement; when the flush writes
    no guarded row, that statement is added too.
  - With deletes, the flush adds one statement per 5,000 deleted episodes and
    one per 5,000 deleted podcasts. The deleted episodes join the
    podcast-lock statement.
  - When a guarded row for a moved episode is written in the same flush, the
    check after the write locks the episode's new podcast: one more statement.
  - ADR 0015's bound for guarded rows (at most five lookups per 5,000
    episodes or pairs) is unchanged.
- **Writes around the flush.** The database refuses deletes. A move written
  around the flush (a Core `UPDATE episodes`) is not carried; only the scan
  prevents one. A trigger could enforce it later.
- **Unaffected paths.** Account deletion, and media merges and splits, neither
  move nor delete episodes or podcasts.
- **Deletion is blocked.** Any future hard-delete or podcast-merge feature
  must handle `ContentSuppressedError` for suppressed rows. A merge can move
  the episodes first, which carries their suppressions, and then delete the
  emptied podcast unless it has its own suppression.
- **Rollout.** Apply revision `0025` before or with the code. Nothing in
  production moves or deletes these rows, so the order is not critical.
  - Existing suppressions are kept unchanged. Only the delete rule of the two
    foreign keys changes.
  - On PostgreSQL the constraints are dropped and added again under their
    default names. Adding them briefly locks `content_suppressions`,
    `podcasts` and `episodes` and checks the existing suppression rows.
  - On SQLite the table is copied with its rows, check constraint and indexes.
- **Rollback.** Revert the change and downgrade to `0024`, which restores
  `ON DELETE CASCADE` and keeps every row. Episode suppressions carried in the
  meantime stay: they are valid suppressions of the same takedown cases.
