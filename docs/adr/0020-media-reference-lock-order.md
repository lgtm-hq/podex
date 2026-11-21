<!-- markdownlint-disable MD041 -->

# 0020. One lock order for every writer of media references

- Status: proposed
- Date: 2026-11-17
- Tags: data, concurrency, media

## Context

Many rows point at a media record: mentions, saves, media alert rules (by
`target_id`, without a foreign key), alert events through their rule, pair
suppressions, mention candidates, provenance snapshots and review items. A media
merge moves or collapses all of them and deletes the merged-away record. A split
moves mentions to a new record and copies pair suppressions.

`merge_ops_media` locked both records `FOR UPDATE`, then the alert rules it
reconciles. The other writers did not follow that order. Reproduced on
PostgreSQL with two concurrent transactions:

- **Rule creation** did not lock the media row. A rule created on the source
  after the merge read its rules was committed pointing at the deleted record. A
  rule created on the target kept a baseline counted before the merge, so the
  next evaluation reported the source's mentions as new.
- **Save and unsave** did not lock the media row. A save of the target waited on
  its foreign-key lock while the merge waited on the save's unique key. An
  unsave of a save the merge had loaded made the merge fail with
  `StaleDataError`. An unsave of the target the merge had not yet seen committed
  made the merge drop the account's source save as a duplicate.
- **Rule deletion and account deletion** deleted a rule's events before the
  rule. The merge locks rules before it updates events, so the two waited on
  each other and PostgreSQL aborted one with a deadlock.
- **Split** took the suppression change counter before the flush that moves its
  mentions locked their podcast. A takedown holds the podcast and takes the
  counter last, so the two deadlocked
  ([ADR 0017](./0017-suppression-identity-changes.md), cycle (d)).

Two reports did not reproduce on `main`:

- [ADR 0015](./0015-durable-takedown-suppression.md) already makes a takedown
  lock the media records it covers `FOR KEY SHARE` before it deletes mentions.
- The flush guard locks the media rows of every mention a flush writes, in one
  ascending statement. So mention publication, single or in one flush, waits for
  a merge of the same record.

The PostgreSQL tests in `tests/postgres/test_media_lock_order_postgres.py` pass
on `main` for these two pairs and are kept as regression tests.

No production route calls the merge yet; the merge route must not ship without
this order.

## Decision

Every writer that reads or writes rows referencing a media record takes its
locks in one total order. It skips the levels it does not need and never takes a
lock of an earlier level after one of a later level:

1. **Media rows**, ascending id, across the writer's whole set, in one call of
   `lock_media` (`podex.services.media_locks`) or `lock_media_rows`
   (`podex.models.suppression_guard`):
   - `FOR UPDATE` (`exclusive`) for merges and splits, which change what a
     record is;
   - `FOR KEY SHARE` for every other writer. It conflicts only with
     `FOR UPDATE`, so these writers do not wait for each other here.

   `lock_media` then re-reads the records. A record a merge deleted while the
   writer waited is absent, and the writer refuses or does nothing. Rows already
   locked as strongly in the transaction are skipped; an exclusive lock also
   covers a later key-share request.

2. **Podcast rows**, ascending id: `FOR SHARE` for writers, `FOR UPDATE` for a
   takedown (ADR 0015).
3. **Episode rows** of moved episodes, `FOR NO KEY UPDATE` (ADR 0017).
4. **Alert rules**, ordered by `(user_id, id)`.
5. **Rows that reference a media record or a rule**: alert events, saves,
   mentions, pair suppressions, candidates, provenance, review items. These are
   written only after the locks above are held. Eligibility and counts are read
   only after them too.
6. **The suppression change counter**, always last (ADR 0015).

This is ADR 0015's order (media, then podcasts, counter last) with ADR 0017's
episode level after podcasts. Alert rules and their children are inserted
between the episode level and the counter.

### Writers

Each writer takes these levels, in this order:

- **Media merge:**
  - 1: both records `FOR UPDATE`, in one statement;
  - 2: the podcasts of every episode mentioning either record;
  - 4: the rules on either record;
  - 5: covered mentions deleted, then events, saves, other references and
    mentions moved;
  - 6: the change counter.
- **Media split:**
  - 1: the record `FOR UPDATE`;
  - 2: the mention-moving flush;
  - 5: mentions moved, then pair suppressions copied;
  - 6: the change counter.
- **Takedown** (unpublish and derivatives): 1 (`FOR KEY SHARE`), 2
  (`FOR UPDATE`), 5, 6. Unchanged from ADR 0015.
- **Guarded flush** (mention publication): 1, 2, 3, then the write. Unchanged
  from ADR 0015 and ADR 0017.
- **Save and unsave:** 1, 5.
- **Alert rule creation** (media): 1, then the eligibility read and the rule
  insert.
- **Alert rule deletion** (media): 1 (the rule's record), 4 (the rule, re-read),
  5 (its events), then the rule. If a merge that committed during its wait moved
  the rule, it locks the rule's new record before the rule. The merge deleted
  the old record, so the earlier lock holds no row and the order holds. It
  re-reads until the target is stable, three reads at most.
- **Account deletion:** 1 (the records the account saved or watches), 4 (its
  rules, in id order), 5.
- **Alert evaluation:** 4 (one account's rules, in id order), 5 (events and
  baselines).

Details:

- **The merge's covered-mention deletion** (ADR 0015) now runs after the rule
  locks. Deleting a mention edits the undelivered alert events that record it,
  and events follow rules. Baselines are still read after the deletion.
- **The merge's podcast locks** are taken right after the media rows. They are
  the same `FOR SHARE` locks the mention-moving flush took before. They are
  taken up front because that flush runs after the rule locks. While both
  records are held no mention of either can be added, so the set is complete.
  The flush then finds them already held.
- **A split** locks its record before it reads the selected mentions. The
  mention-moving flush (levels 1 and 2) runs before the pair suppressions are
  copied and the counter is bumped. Refusals do not change: the guard's origin
  check already refuses moving a mention out of a suppressed pair, which is when
  its copied destination would be suppressed.
- **A writer that has re-read** after its media lock sees what a merge that
  committed meanwhile left:
  - rule creation on a merged-away record answers "not eligible";
  - deleting a rule the merge collapsed answers "not found";
  - saving a merged-away record answers "not found";
  - unsaving a moved save finds nothing on the merged-away record.
- **A mention published after a merge** to the merged-away record fails on its
  foreign key and writes nothing, as before.
- **A takedown waiting behind a merge** answers its retryable 409 when the
  covered mentions moved to a record it did not lock (ADR 0015, unchanged).

A writer that publishes mentions of several records in several flushes of one
transaction must call `lock_media` with the whole set first. No production code
does that today; publication goes through one flush.

### Mail delivery

Digest delivery runs in the transaction that evaluated the account's rules, so
it holds their row locks across SMTP. Digest SMTP now has an explicit socket
timeout (`DIGEST_SMTP_TIMEOUT_SECONDS`, 10 s per operation).

Committing evaluation before delivery needs the per-item transactions of #90:

- The scheduler runs a whole tick in one transaction. A work item committed as
  `running` before delivery has no lease to recover it if delivery then fails.
- The account route rolls back the paid personalisation action when delivery
  fails.

This decision keeps only the timeout. A merge can still wait for mail delivery:
for the rest of the tick in which the account's rules were evaluated, and each
delivery in it is bounded by the timeout.

## Consequences

- **Deadlocks resolved.** Each pair is tested on PostgreSQL in both orders with
  one transaction paused inside its work (`run_paused` in
  `tests/postgres/concurrency.py`):
  - merge with rule creation on either record, and with rule deletion;
  - merge with save and unsave on either record;
  - merge with publication of one or both records' mentions in one flush;
  - merge with a mention takedown on either record;
  - merge with account deletion;
  - split with a takedown of the same podcast. This resolves ADR 0017's cycle
    (d): the counter is the last lock for a split too.
- **Cycles that remain**, reasoned and not tested. In each, PostgreSQL aborts
  one side; a takedown answers its retryable 409.
  - ADR 0017's cycles: writer, move and takedown; (a), (b) and (c).
  - **Several flushes, several records.** A transaction that writes mentions of
    record B in one flush and of record A in a later flush holds B before it
    asks for A. A merge of A and B holds A and waits for B. No production writer
    does this; such a writer must lock its set first.
  - **Podcasts across batches.** A guarded flush locks podcasts in ascending id
    only within each batch of 5,000 episodes (ADR 0015). Share locks do not
    conflict with each other, so no cycle forms through them.
- **Accepted out-of-order paths.** These cannot close a cycle:
  - **Records moved while a writer waited.** Account deletion locks the records
    it read before locking rules. A merge that committed meanwhile moved a rule
    or save to a record it did not lock. It then holds the rules and takes no
    further media lock. A later merge of that record waits for it at the rules.
    Rule deletion re-locks a moved rule's record, and reaches this path only
    after three moves in a row.
  - **Takedowns edit events without rule locks.** A takedown edits alert events
    after its podcast locks and never takes a rule lock. Skipping a level is
    allowed. A merge that touches the same events holds the record the takedown
    needs first, so they meet at the media row.
  - **Enabling or pausing a rule** (`set_alert_rule_enabled`) updates one rule
    row and takes no lock after it.
- **Statement counts.** ADR 0015's bound for a guarded flush is unchanged: at
  most five lookups per 5,000 episodes or pairs. The media lock is the same
  statement, now issued through `Query` so that a test can remove it.
  - **Save, unsave and rule creation:** two more statements each (the lock and
    the re-read).
  - **Rule deletion:** three more (the lock, the re-read and a re-read of the
    rule), and two more for each move it finds.
  - **Account deletion:** four more (two id reads, the lock, the re-read). Its
    rule-id read now locks the rules.
  - **Split:** one more (the lock before its read).
  - **Merge:**
    - Its two per-record lock statements become one lock and one re-read.
    - It adds one episode-id read and one podcast lock per 5,000 episodes.
    - The mention-moving flush no longer runs its own media and podcast locks.
- **SQLite.** Row locks are ignored there, and the extra statements read the
  same rows, so results are unchanged. The default lane checks the statement
  order of each writer (`tests/test_media_lock_order.py`).
- **Rollout.** No migration. New lock waits to watch after deploy:
  - **Saves and rules wait for merges and splits.** Saves, unsaves, rule
    creation and deletion, and account deletion wait while a merge or split of
    the same record runs.
  - **Takedowns wait for splits.** A takedown that covers a record being split
    waits for the split (bounded at 5 s, then 409). Before, it took the podcast
    lock and deadlocked.
  - **Merges wait for digest delivery.** A merge waits for the scheduler's
    digest tick holding the watched rules (see "Mail delivery").
  - **Where it shows.** In `pg_stat_activity` these appear as `Lock` waits on
    `SELECT ... FROM media ... FOR KEY SHARE` and `FOR UPDATE` statements.
- **Rollback.** Revert the change. The merge route must not be enabled without
  it.
- **Numbering.** If another ADR takes 0020 first, rename this file, its heading
  and its index row to the next free number.
