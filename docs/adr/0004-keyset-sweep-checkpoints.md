# 0004. Keyset checkpoints for recurring batched sweeps

- Status: proposed
- Date: 2026-11-14
- Tags: scheduler, retention, schema

## Context

Recurring retention work evaluated a fixed number of unpurged transcripts per
work item, always starting from the lowest ID. Evaluation updates policy fields
but does not purge, so every work item selected the same first batch and later
transcripts were never evaluated. A batched sweep needs a traversal position
that survives process restarts, moves only when a batch commits, and reaches
every row even while rows are inserted, deleted or purged.

A general durable work contract for scheduled work is planned separately. The
traversal fix should not wait for it.

## Decision

Store one row per sweep in `sweep_checkpoints`, keyed by `sweep_key`, holding
`last_id` and `cycle_upper_id`. A sweep runs in cycles:

- A cycle starts when `cycle_upper_id` is empty. It captures the largest
  existing row ID as the upper bound and clears `last_id`.
- Each batch selects eligible rows with `last_id < id <= cycle_upper_id`,
  ordered by ID, limited to the batch size, then records the last ID it
  evaluated.
- A batch shorter than the batch size, or one that reaches the upper bound, ends
  the cycle by clearing both fields, so the next batch starts again from the
  lowest ID.

The checkpoint is read with a row lock and written in the same transaction as
the batch it describes. A rolled-back batch leaves the previous position, so its
rows are evaluated again. Rows inserted during a cycle have IDs above the bound
and wait for the next cycle, so arrivals cannot keep a cycle from finishing. The
keyset compares IDs only, so deleting or purging the checkpoint row, the bound
row or any other row cannot strand the position.

The table starts empty; a missing row starts a new cycle, so no backfill is
needed. We did not keep the position in `pipeline_schedules.metadata_json`,
because schedule reconciliation overwrites that field on every tick.

## Consequences

- Each work item evaluates at most one batch, and a cycle over `N` rows with
  batch size `B` takes about `ceil(N / B)` work items. A row is revisited once
  per cycle rather than on every work item.
- A row added during a cycle is first evaluated in the following cycle, at most
  about two cycles after it arrives.
- Other batched sweeps can reuse the table with their own `sweep_key`. The
  planned durable work contract may later absorb or supersede it.
- Two writers with different traversal rules must not share a `sweep_key`. Pause
  the sweep while switching between incompatible writers.
