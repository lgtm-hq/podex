# CI scripts and workflow contracts

## Scheduled maintenance failure reporting

The suppression check, Scorecard analysis and GHCR cleanup workflows call the
pinned `reusable-main-failure-notifier.yml` after an upstream failure on `main`.
The cleanup dependency covers the complete `prune` matrix. Successful runs and
dispatches from other branches do not report failures. The reusable owns issue
creation and deduplication: repeated failures comment on the open issue for the
same workflow key and branch. Notifier jobs retain blocked egress and grant only
`actions: read`, `contents: read` and `issues: write`.

The issue text is shared with failed pushes: it tells the reader to disarm
pending auto-merges. That convention does not apply to the maintenance keys
`ghcr-cleanup`, `scorecards` and `vuln-suppression-check`, because their
failures do not mean `main` itself is broken. Nothing closes the issue
automatically. Close it by hand after the next green run of the affected
workflow.

For `vuln-suppression-check`, an expired suppression fails every weekly run
until it is dealt with: fix the vulnerability or renew its `ignoreUntil` in
`.osv-scanner.toml` (the job summary lists the IDs). Expect one comment per run
until then; closing the issue earlier only opens a new one on the next run.

Runs that fail at startup, before any jobs run, and cancelled runs are not
reported. In particular, `scorecards.yml` cancels an in-progress run when a new
run starts for the same ref. A Scorecard run whose result upload is rejected by
the Scorecard service but whose job still passes is not reported either.

`tests/bats/ci/test_maintenance_failure_notifier.bats` checks all three callers,
including their full dependencies, conditions, stable keys and permissions. It
replays the conditions with mocked success/failure results, either failed
cleanup matrix leg, and off-main refs. Run it with the complete shell suite:

```sh
bats --recursive tests/bats
```

PR CI validates these contracts, workflow syntax and pin consistency. These
scheduled workflows do not execute on PRs.

### Post-merge dispatch check

Use the Actions page for **Maintenance - GHCR Registry Cleanup** after merge:

1. Dispatch the workflow on `main` with `dry_run=true` and `min_age_days=-1`.
   Expect both `prune` legs to fail and one issue titled
   `fix(ci): main workflow failed: main (ghcr-cleanup)`.
2. Dispatch it again with the same inputs. Expect a comment on the same open
   issue, not a second issue.
3. Dispatch it with the default inputs except `dry_run=true`. Expect a green run
   with the notifier skipped.
4. Close the issue by hand after that green run.

The pinned cleanup script at `808da537a4e346ac1ab3b9e1df10142909b96bc6`
(v0.98.3), `scripts/ci/actions/ghcr-cleanup.sh`, rejects `MIN_AGE_DAYS=-1`
before sourcing helpers or making any API call. The reusable forwards
`min-age-days` and `dry-run` directly to `MIN_AGE_DAYS` and `DRY_RUN`. With
`DRY_RUN=true`, both deletion loops skip deletion and the deletion helper
returns before either DELETE API call. Tagged pruning is disabled by default.
Keep dry-run enabled for every dispatch in this recipe.

This proves the dispatch path, including issue creation and deduplication. The
schedule path is only proven by a real scheduled failure. Do not change
maintenance gates to force a failure.

## Container smoke tests

Run from any working directory with Docker, GNU `timeout` and bash 4.4 or newer
available (the stock macOS bash 3.2 is too old):

```sh
IMAGE=podex-backend:local bash .github/scripts/smoke-backend.sh
IMAGE=podex-frontend:local bash .github/scripts/smoke-frontend.sh
```

`IMAGE` selects the already-built image; `PLATFORM` optionally selects its
architecture. Scripts use the production entrypoint and command, wait up to 60
seconds for Docker health, assert HTTP status and response content, then remove
containers. A 180-second process deadline also bounds Docker operations; TERM
and INT trigger cleanup. Every application container uses `--network none`.

## What the probes prove

Both Dockerfiles probe exact `/health` with Python/Node standard runtime APIs,
without credentials or extra packages. The interval is 30 seconds, each network
operation of the request times out after 2 seconds (about 4 seconds worst case),
Docker timeout 5 seconds, and three consecutive failures mark the container
unhealthy. Backend startup has a 300-second grace period for optional
migrations, with a 5-second start interval so successful startup is recognized
quickly; frontend has 30 seconds. Once startup grace expires, failure is
detected within about 95 seconds. Responses need HTTP 200.

Backend `/health` is HTTP liveness and is exempt from rate limiting at that
exact path. It does **not** query the database. Frontend `/health` is uncached
SSR HTTP liveness and does **not** fetch the backend. These probes distinguish
serving HTTP from a process that is alive but stalled; they do not prove
dependency readiness. The backend scheduler command
(`python -m podex.scheduler_runner`) shares the image but has no HTTP listener.
The probe searches Linux `/proc` command lines for `podex.scheduler_runner`,
including PID 1 and children under shell or init wrappers and Python options
such as `-u`. This exemption does not prove scheduler progress or database
readiness. Other command overrides retain the API probe. The backend probe
assumes port **8000**, matching the default CMD. If an operator changes the
listening port, they must also override the image healthcheck to request exact
`/health` on the new port; changing only the server command is insufficient.

Backend smoke starts from a writable, empty SQLite database in tmpfs, enables
`AUTO_MIGRATE=1`, then reads `/api/v2/podcasts` and asserts an empty page. This
tests the supported default database, Alembic chain, entrypoint, routing and ORM
read; it does not test PostgreSQL or external providers. A second container uses
an inaccessible SQLite path and must exit nonzero with a migration failure
before the server starts. Production auto-migration remains opt-in.

Frontend smoke starts a Node catalog stub on loopback port 8000 inside the same
network-disabled container, preserving the image's build-time API origin. It
checks `/health` and `/` for HTTP 200 and renders the fixture podcast card. The
shared JSON fixture contains one complete `PodcastRead` item; a Vitest checks
its generated API types and required fields, and Astro checks its types in CI.
The smoke asserts that this item's name appears in the rendered page. A backend
fetch failure renders a different banner, so an HTTP 200 error fallback cannot
pass this assertion. This proves SSR integration with the catalog response
shape, not compatibility with a deployed backend or a browser's public API
origin. The podex-ops frontend template currently probes `/`; it is unchanged
here.

## CI contract at lgtm-ci v0.98.3

On PRs, merge groups, main pushes and non-tag manual runs, the pinned reusable's
`health-check-cmd` executes on the runner with `IMAGE` exported. It invokes our
complete smoke script, which starts its own production-command container, waits
for **Docker health**, owns migration settings and network isolation, and
asserts real HTTP route responses. An open port alone does not establish health
or readiness. These single-platform builds run on linux/amd64 and have the
caller checkout. Smoke scripts resolve from that checkout; tooling resolves from
the workflow SHA without a caller `tooling-ref`.

`smoke-test-script` is forwarded only to the split path. Tag publishes request
linux/amd64 and linux/arm64; `verify-per-platform` checks out this repository,
pulls each staging image by immutable digest, then invokes the smoke script with
`IMAGE`, `PLATFORM`, and `REGISTRY`. Each script waits for the image's Docker
health and checks its real routes. Smoke failure blocks manifest merging.

All three health-hook inputs (command, port and timeout) are empty on `v*.*.*`
tag refs, including manual runs on those tags. The split publish health job has
no caller checkout, so it cannot run these repository scripts and stays skipped.
The pinned classifier accepts empty health inputs: an empty port is valid and
timeout parsing is conditional on a nonempty health command. The publish job
requires a nonempty health command; merge accepts its skipped result while still
requiring smoke success. Split validation also conditions its health step on a
nonempty command, and its smoke step on `validate-on-pr`; current tag runs
publish instead of validating. The standalone smoke reusable requires a
published digest and cannot validate a PR-local image. The main failure notifier
depends on both parent image jobs, including their smoke gates. It does not run
for tag failures.

Runner image pulls and builds remain under the existing block-mode allowlists.
Both `replace` lists retain every host of the pinned Docker egress preset, plus
the existing application build endpoints. The smoke containers themselves cannot
reach any external host. No additional endpoint is required.

The PR/main check names before and after are identical:

```text
🐳 Backend Image / Classify Platforms
🐳 Backend Image / Docker build / Build and Push
🐳 Backend Image / Docker build / Vulnerability Scan
🐳 Backend Image / Docker multi-platform
🎨 Frontend Image / Classify Platforms
🎨 Frontend Image / Docker build / Build and Push
🎨 Frontend Image / Docker build / Vulnerability Scan
🎨 Frontend Image / Docker multi-platform
notify-failure
```

On tags the existing split verification job base names become active for each
linux/amd64 and linux/arm64 matrix entry (GitHub appends matrix suffixes):

```text
🐳 Backend Image / Docker multi-platform / Publish / Docker verify per platform
🎨 Frontend Image / Docker multi-platform / Publish / Docker verify per platform
```

No check is added to a ruleset. The existing all-checks gate accepts completed
skipped jobs and waits for running jobs; PRs gain no tag-only wait. Caller
permissions already cover every nested job's request, including `actions: read`
for SARIF, `packages: write`, `id-token: write`, and `attestations: write`.

These are additive probes and smoke gates. Roll back by reverting this change or
deploying the previous image digest; no production migration or backfill is
added.
