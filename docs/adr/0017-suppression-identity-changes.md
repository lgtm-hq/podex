<!-- markdownlint-disable MD041 -->

# 0017. Moves and deletes that would lift a suppression are refused

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

This decision extends ADR 0015. The flush guard refuses both changes instead
of carrying a suppression to the new row (`podex.models.suppression_identity`,
called from `podex.models.suppression_guard`).

### Moves

A change of `Episode.podcast_id`, by column, relationship or collection
assignment, is refused in two cases.

- **A suppression is involved.** The podcast the episode leaves (the one the
  session remembers) or the podcast it joins has a podcast suppression. The
  guard counts suppressions already stored and those pending in the same
  session (`session.new`). It raises `ContentSuppressedError` naming the
  episode, for example `Episode 5 (moved from podcast 1 to podcast 2; a
  takedown suppresses podcast 1)`.
  - Moving into a suppressed podcast is refused too. Nothing in the product
    does it.
  - Moves between two podcasts without a suppression stay allowed. An
    episode's own episode and pair suppressions are keyed by its id and
    follow it, so they do not block such a move.
  - A podcast created in the same flush has no suppression yet. Only the
    podcast the episode leaves is checked then.
- **The session's copy is stale.** The database no longer has the episode in
  the podcast the session remembers, because another change moved it since
  the copy was loaded. The guard raises `RetryableLockError` (a `ValueError`,
  answered as a 409) naming the episode; the caller reloads and retries.

The remembered podcast comes from the column's active history: assigning the
column of a row a commit expired loads the replaced value first. A relationship
assignment reaches the column only during the flush, which still holds the
remembered value when the guard reads it. Re-assigning the podcast an episode
already has leaves no attribute history and is not a move.

A permitted move cannot change what is published. Neither podcast has a
podcast suppression, and the episode's own suppressions follow it. So a move
does not bump the change counter and takes no counter lock.

**Lock order for a move.** It extends ADR 0015's order (media rows, then
podcast rows, counter last) with the episode row after the podcast rows:

1. media rows of guarded mentions in the flush, `FOR KEY SHARE`;
2. podcast rows `FOR SHARE`, ascending id. First, as before, the podcasts of
   episodes with guarded rows and of deleted episodes, through their persisted
   `podcast_id`. Then the podcasts the moves leave, as the session remembers
   them, and join, by id;
3. the rows of the moved episodes, `FOR NO KEY UPDATE`, ascending id, reading
   each episode's persisted `podcast_id` in the same statement. On SQLite,
   which ignores row locks, the database write lock is taken first with the
   same no-op write that authorisation uses;
4. if a persisted podcast differs from the remembered one, the move is refused
   as stale here. No podcast found through the episode row is ever locked
   after it;
5. the coverage check of both podcasts. Only then does the flush write.

A takedown of either podcast takes it `FOR UPDATE`. So a takedown that holds
it first commits, and the move, which waited, is then refused. A move that
holds it first commits, and the takedown, which waited, then covers what the
podcast holds after the move. `FOR NO KEY UPDATE` on the episode is the lock
the move's own `UPDATE` takes anyway; two moves of one episode therefore run
one after the other, and the second is refused as stale.

**Why this order agrees with discovery.** Discovery updates a podcast row and
then its episode rows in one flush; the unit of work writes the parent before
the child. A move also takes the podcast before the episode, so within those
flushes one side waits at the podcast row:

- A move that waits for discovery's podcast row holds no episode row yet.
- Discovery waiting for a move's `FOR SHARE` on the podcast holds no episode
  row of that flush yet.

Because the move never locks a podcast after an episode row, refusing a stale
origin instead, no podcast-then-episode cycle forms with discovery.

**Remembered locks and checks.** A move does not rely on anything cached: it
locks the podcasts it names and the episode row itself, and reads coverage
fresh. The guard's caches are:

- **Passed checks:** kept until the flush ends. They are forgotten earlier
  when this session writes a suppression row.
- **Podcast locks for guarded rows' episodes:** kept until the flush ends.
- **Authorisation locks** (`lock_episode_text`): kept until the end of the
  next flush, or of the transaction if no flush follows.
- **Media locks:** kept until the transaction or savepoint ends.

Another transaction can move an episode between two flushes of this one. The
next flush therefore locks and checks the podcast the episode is in then.

**Takedown execution with a pending move.** With autoflush off, a session may
hold an unflushed move when it executes a takedown of the podcast the episode
leaves or joins. Recording the suppression flushes the move with the pending
suppression in `session.new`, and the flush refuses the move. Nothing is
committed; once the move is undone, the takedown runs again.

### Deletes

- **Through the ORM.** Before the flush writes, the guard refuses to delete:
  - an `Episode` covered by its own suppression, a pair suppression of it, or
    its podcast's suppression;
  - a `Podcast` covered by its own suppression or any of its episodes'.

  Suppressions stored and pending in the same flush count. A takedown that
  records its suppression in the flush that also deletes the row therefore
  refuses the delete. `ContentSuppressedError` names the row, such as
  `Podcast 3 (deleted while a takedown suppression covers it)`. It now takes
  an optional episode id and a podcast id, for a podcast with no covered
  episode.
- **In the database.** Revision `0026` makes both foreign keys of
  `content_suppressions` `ON DELETE RESTRICT`. The database refuses deleting
  only rows a suppression names directly: a suppressed podcast, a suppressed
  episode, the episode of a pair suppression, and episodes a podcast delete
  cascades to when one of them is named.
- **Not in the database.** An episode covered only by its podcast's
  suppression is referenced by no suppression row. A Core or raw `DELETE` of
  it succeeds in the database, and a new episode may then reuse its id. Only
  the ORM guard and the structural scan stop that. A database trigger is a
  possible later hardening; this decision does not add one.

A partial flush (`session.flush([episode])`) does not skip either rule. The
guard reads the session's moves, deletes and pending suppressions whatever
the flush is limited to.

### Structural scan

`tests/test_guarded_table_writes.py` refuses writes that skip the flush, for
the guarded tables and for `Episode` and `Podcast`.

- **Forms covered:** Core and dialect `insert`, `update` and `delete`
  (upserts included), `__table__` methods, `Query.update` and
  `Query.delete`, `bulk_*` session methods, and raw SQL.
- **Targets:** read positionally or as `table=`, through `aliased(...)` or
  `__table__`, or named by string.
- **Raw SQL:** read from string literals and from the literal parts of
  f-strings and of `%`, `.format` and `+` expressions, positionally or as
  `text=`. Comments are removed first. Targets may be quoted,
  schema-qualified or follow `ONLY`. `INSERT`, `INSERT OR ...`, `REPLACE`,
  `MERGE`, `UPDATE`, `DELETE`, `TRUNCATE` and `COPY ... FROM` count, and
  every write in a string is reported before exemptions apply.
- **Exemptions** name exact verbs and tables. Takedown execution keeps its
  bulk deletes of derivative runs, semantic chunks and episode summaries,
  and its bulk update of transcripts.

Limits:

- SQL or a table object held in a variable is not followed.
  `podex.suppression_backfill` writes that way; its writes go to the
  suppression tables, which the scan does not cover.
- An ORM bulk statement run through the session
  (`session.execute(update(Episode)...)`) skips the flush guard at run time.
  Only the scan refuses it.
- Code outside `backend/src`, `backend/scripts` and `backend/alembic` is not
  scanned.

### Not built: a real move or deletion of suppressed content

A suppression does not record a stable identity (feed URL, episode GUID), so
it cannot follow content to a new row or recognise it when ingested again.
Deleting a suppressed row and ingesting it again is refused at the delete,
not tracked afterwards. Moving or deleting suppressed content for real needs
an operator action with its own audit trail, which does not exist:

- **Moving:** re-key the suppression to the episode in its new podcast, in
  the same transaction as the move.
- **Deleting:** either carry a stable identity on the suppression, checked
  at ingestion, so content ingested again stays refused after the row is
  gone; or lift the suppression with a recorded decision that the content may
  return. ADR 0015 already notes that lifting does not exist.

Until then, archiving is how a suppressed podcast is retired.

## Consequences

- **Cost.** A flush that moves and deletes nothing runs no added statement;
  moves and deletes are found in memory.
  - **Moving flush.** One podcast-lock statement per 5,000 podcasts, one
    episode-lock statement per 5,000 episodes, and one coverage read per
    5,000 podcasts. That is three `SELECT`s for any number of moves within
    those batches, plus the no-op write on SQLite.
  - **Deleting flush.** The deleted episodes join the podcast-lock statement,
    plus one coverage read per 5,000 deleted episodes and per 5,000 deleted
    podcasts. When the same flush also records suppressions, up to one more
    read per 5,000 deleted episodes and per 5,000 deleted podcasts.
  - **ADR 0015's bound** for guarded rows is unchanged: at most five lookups
    per 5,000 episodes or pairs in each flush.
- **Known deadlock.** This protocol is not deadlock-free. One cycle, reasoned
  rather than tested:
  1. A writer updates an existing derivative row of episode E and flushes. It
     holds that row and podcast A `FOR SHARE`.
  2. A move of E from A to B commits; neither podcast is suppressed yet.
  3. A takedown of B takes B `FOR UPDATE` and waits to delete the writer's
     row.
  4. The writer's second flush for E waits for B.

  PostgreSQL aborts one side, or the takedown's lock wait expires. The
  takedown answers its retryable 409; an ordinary writer sees a database
  error.
- **Residual race.** A row can be written for an episode that is then
  suppressed, without the takedown deleting it, in two ways:
  - **Takedown of the new podcast.** A writer locks the episode's podcast A
    and checks. A concurrent move of the episode to B commits, then a
    takedown of B commits after the writer's check and before the writer's
    commit. The window runs to the writer's commit. The takedown did not
    see the writer's uncommitted row, and the writer never locked B.
  - **Episode or mention takedown.** The takedown resolves the episode's
    podcast before it locks it. If a move commits in between, it locks the
    old podcast, and a writer of the moved episode, which locks the new one,
    is not ordered against it.

  What such a row means:
  - **Mentions** are hidden on every read path, because all of them use one
    predicate (`published_mention_clause`).
  - **Derivative rows** are read by no route today.
  - **The retention coverage check** (`services/derivative_coverage.py`)
    counts summaries, chunks and graph triples without the predicate.
  - **Provider authorisation** pins the episode's podcast, not the episode's
    membership of it. So a provider call can be made for an episode that was
    moved and then taken down while the call was being authorised and made.
- **Writes around the flush.** The database refuses deletes of rows a
  suppression names. Everything else around the flush is caught only by the
  structural scan: a move by Core or raw `UPDATE`, an upsert, a delete of an
  episode covered only by its podcast.
- **Unaffected paths.** Account deletion, and media merges and splits, neither
  move nor delete episodes or podcasts. Discovery does not reassign episodes
  to another podcast.
- **Deletion and merges are blocked.** Any future hard-delete or
  podcast-merge feature must handle `ContentSuppressedError` for suppressed
  rows, and the operator action above.
- **Rollout.** Apply revision `0026` before or with the code. Nothing in
  production moves or deletes these rows, so the order is not critical.
  - Existing suppressions are kept unchanged. Only the delete rule of the two
    foreign keys changes.
  - On PostgreSQL the constraints are dropped and added again under their
    default names. Adding them briefly locks `content_suppressions`,
    `podcasts` and `episodes` and checks the existing suppression rows.
  - On SQLite the table is copied with its rows, check constraint and indexes.
- **Rollback.** Revert the change and downgrade to `0025`, which restores
  `ON DELETE CASCADE` and keeps every row.
