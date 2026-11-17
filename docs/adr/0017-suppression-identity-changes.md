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
  session remembers) or the podcast it joins has a podcast suppression.
  - The guard counts suppressions already stored and those pending in the
    same session (`session.new`); see "What pending covers" for the limit.
  - It raises `ContentSuppressedError` naming the suppressed podcast and the
    episode, for example `mentions for podcast 2 are suppressed by an
    approved takedown; refused write: Episode 5 (moved from podcast 1 to
    podcast 2)`. A podcast the same flush creates is named `a new podcast`.
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
  answered as a 409) naming the episode. The caller rolls back, reloads the
  episode and retries.

Whether a change is a move is read from attribute history alone, so changing
other columns of an episode loads nothing, even when `podcast_id` is deferred
or expired. The remembered podcast comes from the column's active history:
assigning the column of a row a commit expired loads the replaced value first.
A relationship assignment reaches the column only during the flush, which
still holds the remembered value when the guard reads it. Re-assigning the
podcast an episode already has leaves no attribute history and is not a move.

A permitted move cannot change what is published. Neither podcast has a
podcast suppression, and the episode's own suppressions follow it. So a move
does not bump the change counter and takes no counter lock.

**Lock order for a move.** It extends ADR 0015's order (media rows, then
podcast rows) with the episode row after the podcast rows:

1. media rows of guarded mentions in the flush, `FOR KEY SHARE`;
2. podcast rows `FOR SHARE`, ascending id:
   - first, as before, the podcasts of episodes with guarded rows and of
     deleted episodes, through their persisted `podcast_id`;
   - then, by id, the podcasts the flush deletes and those the moves leave
     (as the session remembers them) and join;
3. the rows of the moved episodes, `FOR NO KEY UPDATE`, ascending id, reading
   each episode's persisted `podcast_id` in the same statement. On SQLite,
   which ignores row locks, the database write lock is taken first with the
   same no-op write that authorisation uses;
4. if a persisted podcast differs from the remembered one, the move is refused
   as stale here. The move does not lock the podcast it found this way;
5. the coverage check of both podcasts. Only then does the flush write.

After the write, the guard's check of guarded rows can lock the podcast of a
row written in the same flush. That podcast lock then follows the episode
rows of step 3; see cycle (c) below.

A takedown of either podcast takes it `FOR UPDATE`. So a takedown that holds
it first commits, and the move, which waited, is then refused. A move that
holds it first commits, and the takedown, which waited, then covers what the
podcast holds after the move. `FOR NO KEY UPDATE` on the episode is the lock
the move's own `UPDATE` takes anyway. Two moves of one episode therefore run
one after the other, and the second is refused as stale.

**Why this order agrees with discovery.** Discovery updates a podcast row and
then its episode rows in one flush; the unit of work writes the parent before
the child. A move also takes the podcast before the episode, so within those
flushes one side waits at the podcast row:

- A move that waits for discovery's podcast row holds no episode row yet.
- Discovery waiting for a move's `FOR SHARE` on the podcast holds no episode
  row of that flush yet.

The move refuses a stale origin instead of locking the podcast it found
through the episode row, so this pair does not form a podcast-then-episode
cycle. Other cycles exist; see "Deadlocks".

**Remembered locks and checks.** As in ADR 0015:

- a passed check is kept until the transaction or savepoint ends, and
  forgotten as soon as this session writes a suppression row;
- a podcast lock taken for an episode (by a guarded flush or by authorisation)
  is kept until the transaction or savepoint ends;
- media locks are kept until the transaction or savepoint ends.

Repeated flushes that move nothing therefore reuse them. A move never relies
on them: it locks the podcasts it names and the episode row itself, and reads
coverage fresh. Another transaction can still move an episode after this one
remembered its podcast lock; "Residual race" covers what that allows.

**Takedown execution with a pending move.** With autoflush off, a session may
hold an unflushed move when it executes a takedown of the podcast the episode
leaves or joins. Recording the suppression flushes the move with the pending
suppression in `session.new`, and the flush refuses the move. Nothing is
committed. Once the move is undone, the takedown runs again.

### Deletes

- **Through the ORM.** Before the flush writes, the guard refuses to delete:
  - an `Episode` covered by its own suppression, a pair suppression of it, or
    its podcast's suppression;
  - a `Podcast` covered by its own suppression or any of its episodes'.

  The guard locks a podcast it deletes `FOR SHARE` before reading its
  coverage. A takedown that holds the podcast is therefore seen once it
  commits, and the delete is refused with `ContentSuppressedError` rather
  than failing on the foreign key. Suppressions stored and pending in the
  same flush count; see "What pending covers". `ContentSuppressedError`
  names the row, such as `Podcast 3 (deleted while a takedown suppression
  covers it)`.
- **In the database.** Revision `0026` makes both foreign keys of
  `content_suppressions` `ON DELETE RESTRICT`, and the models declare the
  same rule. The database refuses deleting only rows a suppression names
  directly: a suppressed podcast, a suppressed episode, the episode of a pair
  suppression, and episodes a podcast delete cascades to when one of them is
  named.
- **Not in the database.** An episode covered only by its podcast's
  suppression is referenced by no suppression row. A Core or raw `DELETE` of
  it succeeds in the database, and a new episode may then reuse its id. Only
  the ORM guard and the structural scan stop that. A database trigger is a
  possible later hardening; this decision does not add one.

A partial flush (`session.flush([episode])`) does not skip either rule. The
guard reads the session's moves, deletes and pending suppressions whatever
the flush is limited to.

### What pending covers

The rules count suppressions already stored and those in the flush that
records them. They do not cover a move or delete flushed before the
suppression is recorded, in the same transaction or an earlier one:

- **Move.** If the move was flushed first, the episode is no longer in the
  podcast when the takedown executes. A podcast takedown covers the
  podcast's episodes as of its execution, so it does not cover that episode.
- **Delete.** If the delete was flushed first, the row is gone, and the
  takedown records nothing for it.
- **Autoflush.** With autoflush on, the takedown's first query flushes a
  pending move or delete, so the same happens without an explicit flush.
  The application's sessions run with autoflush off (`SessionLocal`).

The ops route approves and executes a takedown in one transaction, so
"approved but not executed" is never a committed state there. This is the
same as before this decision: a takedown always covered what its subject
held when it executed.

### Structural scan

`tests/test_guarded_table_writes.py` refuses writes that skip the flush, for
the guarded tables and for `Episode` and `Podcast`.

- **Forms covered:** Core and dialect `insert`, `update` and `delete`
  (upserts included), the `Insert`, `Update` and `Delete` classes,
  `__table__` methods, `Query.update` and `Query.delete`, `bulk_*` session
  methods, and raw SQL.
- **Targets:** read positionally or as `table=`, through `aliased(...)` or
  `__table__`, or named by string.
- **Raw SQL** passed to `execute`, `exec_driver_sql`, `executemany`,
  `executescript` or `text` is read from:
  - string and bytes literals;
  - the literal parts of f-strings and of `%`, `.format`, `+` and
    `sep.join([...])`;
  - literals passed through a text-returning string method (`.strip()`,
    `.replace(...)`, ...) or `dedent`.
- **Reading SQL:** string literals and comments are tokenised, so a comment
  marker inside a literal hides nothing. Targets may be quoted,
  parenthesised, qualified by up to two names or follow `ONLY`. `INSERT`,
  `INSERT OR ...`, `REPLACE`, `MERGE`, `UPDATE`, `DELETE`, `TRUNCATE` (every
  table it lists) and `COPY ... FROM` count. Every write in a string is
  reported before exemptions apply.
- **Exemptions** grant exact writes: verb, table, the columns an update sets,
  and the number of occurrences. A test checks the exempt module still makes
  exactly those writes. Takedown execution keeps its bulk deletes of
  derivative runs, semantic chunks and episode summaries, and its bulk
  update of the four derived-text columns of transcripts.
- **Migrations.** ADR 0015's scan exempted `alembic/versions` entirely. That
  exemption is removed, which is stricter: migrations are scanned like any
  other code. A future migration that must write a scanned table outside the
  ORM adds an exemption for exactly that write (verb, table, columns, count)
  with its reason, and says why the write cannot lift a suppression.

Limits:

- A write built dynamically is not followed: a model or table chosen at run
  time, SQL or a table object held in a variable, SQL assembled in a loop, or
  a table name a replacement inserts (`"UPDATE x".replace("x", name)`).
  `podex.suppression_backfill` writes through a table object held in a
  variable; its writes go to the suppression tables, which the scan does not
  cover.
- A write through a helper defined in another package, or through a call the
  scan does not know returns text, is not seen.
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

- **Cost.** A flush that moves and deletes nothing runs no added statement,
  and keeps ADR 0015's caching. Moves and deletes are found in memory, from
  attribute history and the session's deleted rows.
  - **Repeated flushes.** Five flushes of one mention each for one episode,
    with a new media record each time, run 3, 2, 2, 2, 2 `SELECT`s. A change
    of an episode's other columns runs none.
  - **Moving flush.** One podcast-lock statement per 5,000 podcasts (it also
    covers podcasts the flush deletes), one episode-lock statement per 5,000
    episodes, and one coverage read per 5,000 podcasts. That is three
    `SELECT`s for any number of moves within those batches, plus the no-op
    write on SQLite.
  - **Deleting flush.** The deleted episodes join the podcast-lock statement
    of guarded rows. Deleted podcasts are locked by id, in the statement
    above. There is one coverage read per 5,000 deleted episodes and per
    5,000 deleted podcasts. When the same flush also records suppressions,
    up to one more read per 5,000 deleted episodes and per 5,000 deleted
    podcasts.
  - **ADR 0015's bound** for guarded rows is unchanged: at most five lookups
    per 5,000 episodes or pairs in each flush.
- **Deadlocks.** This protocol is not deadlock-free. The cycles below are
  reasoned, not tested. In each, PostgreSQL aborts one side, or a takedown's
  lock wait expires. A takedown answers its retryable 409; an ordinary
  writer or mover sees a database error.
  - **Writer, move, takedown.**
    1. A writer updates an existing derivative row of episode E and flushes.
       It holds that row and podcast A `FOR SHARE`.
    2. A move of E from A to B commits; neither podcast is suppressed yet.
    3. A takedown of B takes B `FOR UPDATE` and waits to delete the writer's
       row.
    4. The writer's second flush for E waits for B.
  - **(a) Podcast edit and move in one flush.** One flush edits podcast P
    and moves its episode E; another transaction moves E too.
    1. The first flush takes P `FOR SHARE`, then E `FOR NO KEY UPDATE`.
    2. The other takes P `FOR SHARE` and waits for E.
    3. The first flush's `UPDATE` of P then waits for the other's share lock
       on P.
  - **(b) Moves across flushes.** Two transactions move the same episodes in
    several flushes, in opposite order. Ascending order holds only within a
    flush.
  - **(c) A guarded row written in the moving flush.** A moving flush that
    also inserts a guarded row for an episode it knows only after the flush
    locks that episode's podcast after the episode rows of step 3.
  - **(d) Media split.** On `main` already and unchanged here: a media split
    can take the change counter before its mention-moving flush locks
    podcast rows. So "the counter is the last lock" does not hold for split.
- **Residual race.** A row can be written for an episode that is then
  suppressed, without the takedown deleting it, in two ways:
  - **Takedown of the new podcast.** A writer locks the episode's podcast A
    and checks, possibly in an earlier flush whose lock and check it still
    remembers. A concurrent move of the episode to B commits, then a
    takedown of B commits after the writer's check and before the writer's
    commit. The window runs to the writer's commit. The takedown did not see
    the writer's uncommitted row, and the writer never locked B.
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
