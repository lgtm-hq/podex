<!-- markdownlint-disable MD041 -->

# 0012. Every main commit keeps its own validation run

- Status: accepted
- Date: 2026-11-16
- Tags: ci

## Context

The validation workflows (quality, tests, frontend, Docker, SBOM, CodeQL, OSV
audit, pin sync guard and the all-checks gate) used one concurrency group per
ref. On `main` they never cancelled an in-progress run, but GitHub keeps only
one pending run per group: a newly queued run replaces it. When three pushes to
`main` overlapped, the middle commit's run was cancelled before any job started,
and its all-checks gate then failed on that cancelled run. The infra-failure
re-run workflow re-runs failures only, so that commit never got a result.
Release workflows use repository-wide groups because they mutate versions and
tags, and must stay serialized.

## Decision

A push to `main` in a validation workflow gets a concurrency group of its own by
appending `-<sha>` when
`github.event_name == 'push' && github.ref == 'refs/heads/main'`, and keeps
`cancel-in-progress` false. Every other event keeps the group without the
suffix: pull requests still cancel superseded runs, merge queue entries and tags
keep their own refs, and manual or scheduled runs on `main` keep the shared
`main` group.

Release mutators (`release-version-pr`, `release-auto-tag`) and scheduled
maintenance are deliberately left as they are. Making them concurrent per commit
would allow concurrent release mutations.

Every workflow file is assigned to one class (main validation, release mutation,
scheduled upkeep, pull request only, follows other runs) in a BATS contract
test. The test evaluates the groups with GitHub's expression semantics and
replays them through the queueing rule.

## Consequences

- Each main commit gets a completed run of every validation workflow, so a
  validation result belongs to the commit it ran on. The commit's all-checks
  gate can still fail for a reason outside its validation (next point).
- Release workflows keep their serialization and their pending-run replacement.
  With three pushes inside one release run, the middle commit's release runs are
  replaced, its gate fails on the cancelled run although validation passed, and
  a replaced auto-tag run for a release commit never cuts that tag. This is
  tracked on #45; changing it needs a separate decision.
- The pinned main failure notifier was serialized by the shared `main` group.
  Two failing commits of one workflow can now run it at once and open two issues
  for one workflow key. The fix belongs in the reusable notifier.
- Code-scanning uploads for two main commits can now finish out of order. Each
  upload names the commit it analysed, but which upload GitHub treats as the
  current state of `main` when an older commit's analysis arrives last is not
  established. If it is the last to arrive, alert state can lag one commit
  until the next push.
- A burst of merges runs that many validation runs, and that many polling gates,
  in parallel instead of at most two per workflow: about 29 to 33 run-minutes
  per commit across the nine workflows, so N overlapping merges add roughly
  (N-2) times that. Runner minutes and the shared API quota grow with bursts.
- A new workflow file fails the contract test until it is classified, and a new
  validation workflow that runs on pushes to `main` must use the per-commit
  group. The test cannot see `concurrency` inside the pinned reusables, so a pin
  bump needs that checked by hand.
