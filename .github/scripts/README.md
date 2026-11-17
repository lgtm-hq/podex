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
(`python -m podex.scheduler_runner`) shares the image but has no HTTP listener,
so the backend probe decides the container's role before probing:

1. `PODEX_PROCESS_ROLE`, read from the probe's own environment (Docker gives the
   probe the container's environment). Accepted values are `api` (always perform
   the HTTP check) and `scheduler` (never), compared after removing surrounding
   whitespace and ignoring case, so `Scheduler` is `scheduler`. An empty or
   whitespace-only value is treated as unset. Any other value is unhealthy, with
   one line naming the accepted values.
2. When it is unset, PID 1's argument vector, with a closed rule. Shell scripts
   are never interpreted. Up to four launchers are removed from the front: the
   image entrypoint (`/app/scripts/start.sh`, also as
   `/bin/sh /app/scripts/start.sh` before it execs), `tini` or `docker-init`
   (options `-s`, `-g`, `-w`, `-v`, `-vv`, `-vvv`, `-p SIGNAL`, `-e CODE`, then
   an optional `--`), `dumb-init` (`-c`, `--single-child`, `-v`, `--verbose`,
   `-r`/`--rewrite FROM:TO`, then an optional `--`) and `env` followed only by
   `NAME=value` assignments. The container is the scheduler only when what
   remains is one of:

   - `python [-u|-B|-O|-OO ...] -m podex.scheduler_runner [arguments]`, where
     the interpreter's file name is `python`, `python3` or `python3.N`;
   - `sh`, `bash`, `dash` or `ash`, then `-c`, then a script that, without
     surrounding whitespace, is exactly that invocation, optionally preceded by
     `exec`, and contains none of ``; & | < > ( ) $ ` " ' \ # * ? [ ] { } ~ !``
     or a newline. No other shell option and no argument after the script is
     accepted.

   Arguments after the module name are not examined, whatever their bytes.

3. PID 1 grants this exemption only when it belongs to the probe's own
   container: the probe's `/proc/self/cgroup` and PID 1's `/proc/1/cgroup` must
   both be readable, at most 8 KiB, made of well-formed
   `hierarchy-ID:controllers:/path` lines (one `0::/path` line on cgroup v2, one
   line per hierarchy on v1) and identical. Docker runs the HEALTHCHECK command
   inside the container's cgroup, the one its PID 1 is in, with either cgroup
   version and with a private or host cgroup namespace. In a PID namespace
   shared with another container (`--pid=container:<name>`, a pod), PID 1 is
   that container's process and its cgroup differs: it shows as another absolute
   path, or as a `/..`-relative one from a private cgroup namespace. With
   `--pid=host` PID 1 is the host's init. When ownership cannot be established
   (a difference, an unreadable, oversized or malformed file), the probe
   performs the HTTP check.

Everything else gets the HTTP check, including compound scripts
(`sh -c 'python -m podex.scheduler_runner & wait'`), script files, `bash -lc`,
`uv run` or `poetry run`, `env` with options and combined Python flags such as
`-um`. Those scheduler deployments must set `PODEX_PROCESS_ROLE=scheduler`.
Containers in a shared PID namespace (`--pid=container:<name>`, `--pid=host`,
pods) should always set the role explicitly, `PODEX_PROCESS_ROLE=api` or
`PODEX_PROCESS_ROLE=scheduler`: detection cannot establish that PID 1 is theirs,
so without the setting a scheduler there is reported unhealthy. Deployments that
start the scheduler in exec form, through the entrypoint, an init process or a
single-command `-c` script as above, in their own PID namespace, stay healthy
without it.

No other process is read. A scheduler started with `docker exec` in an API
container, a `grep` or `pgrep -f` for the module name, a scheduler re-parented
to PID 1 after the process that started it exited, or a scheduler that is PID 1
of another container sharing the PID namespace leaves the API's HTTP check in
force. Reading is bounded: at most 16 KiB of command line, 64 arguments, four
launchers and 8 KiB per cgroup file; anything larger, an unreadable or empty PID
1 command line, or any error while deciding keeps the HTTP check. Any exception
raised inside the probe, including an inner `SystemExit`, exits 1 with one line
on stderr; an interrupt (`KeyboardInterrupt`) is not converted and still ends
the probe with a non-zero status. The scheduler role does not prove scheduler
progress or database readiness.

Probe requests to `/health` stay in the `podex.access` log: one line per probe
(every 30 seconds, every 5 seconds during start-up) shows when probes ran, their
status and latency, which is the evidence needed when a container turns
unhealthy, and it is the same record an external load-balancer check leaves.
Filtering by path would also hide those external checks. The backend probe
assumes port **8000**, matching the default CMD. If an operator changes the
listening port, they must also override the image healthcheck to request exact
`/health` on the new port; changing only the server command is insufficient.

Backend smoke starts from a writable, empty SQLite database in tmpfs, enables
`AUTO_MIGRATE=1`, then reads `/api/v2/podcasts` and asserts an empty page. This
tests the supported default database, Alembic chain, entrypoint, routing and ORM
read; it does not test PostgreSQL or external providers. Four scheduler
containers start the same way: `python -m podex.scheduler_runner` (exec form),
`sh -c 'exec python -u -m podex.scheduler_runner'` and
`sh -c 'python -u -m podex.scheduler_runner'`, all detected without
configuration, and the compound
`sh -c 'python -u -m podex.scheduler_runner & wait'` with
`PODEX_PROCESS_ROLE=scheduler`. Each must become Docker healthy, have exec'd
past the entrypoint, and have a scheduler module process running; the smoke
prints the PID 1 command line it observed. With `exec`, PID 1 ends up as Python.
In the single-command form without `exec` the image's `sh` stays PID 1, and the
smoke asserts that, so the `-c` script rule is proven against the built image as
well as by unit tests. In the compound form the shell also stays PID 1. The
healthy scheduler cases also prove against real Docker that the HEALTHCHECK
process shares PID 1's cgroup. Two API-shaped containers that never listen on
port 8000 prove the negative. In the first, PID 1 is a Python process that
starts a real scheduler as its child, and the smoke starts another with
`docker exec`. The second runs the same compound `sh -c` without the role
setting. With health timing overridden on `docker run`
(`--health-start-period=1s --health-interval=2s --health-retries=1`; the image's
5-second start interval still applies to the first probe), Docker must report
both unhealthy within seconds rather than after the 300-second start period.
Every recorded probe must have exited 1 without output, showing the HTTP check
ran rather than the probe crashing, and their scheduler processes must still be
running afterwards. Another container uses an inaccessible SQLite path and must
exit nonzero with a migration failure before the server starts. Its log must
name the failure by error class and SQLite error name
(`OperationalError (OperationalError, SQLITE_CANTOPEN) [driver message withheld]`)
and must not contain the driver's own message, which the application withholds
because it can carry row values. Production auto-migration remains opt-in.

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

Tag publishes request linux/amd64 and linux/arm64 and take the split path. They
run no smoke today, because neither of the pinned reusable's two hooks works for
this repository there:

- The split health job has no caller checkout, so it cannot run these repository
  scripts (lgtm-hq/lgtm-ci#1572). All three health-hook inputs (command, port
  and timeout) are therefore empty on `v*.*.*` tag refs, including manual runs
  on those tags, and that job stays skipped. The pinned classifier accepts empty
  health inputs: an empty port is valid and timeout parsing is conditional on a
  nonempty health command.
- `smoke-test-script` runs in `verify-per-platform`, which pulls each staging
  image by a digest read from a run artifact named `staging-digest-<platform>`.
  Both image jobs of one run write that same name, so depending on upload order
  one image's job can pull the other image's digest, which fails
  (lgtm-hq/lgtm-ci#1579). With the input set, v0.41.26 published the frontend
  image and no backend image. The input is not passed until that is fixed
  upstream and the pin is bumped (#822).

A tag therefore publishes the same Dockerfiles that the PR and `main` builds of
the same commit smoked on linux/amd64; linux/arm64 images are not smoked. The
main failure notifier depends on both parent image jobs. It does not run for tag
failures.

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

No check is added to a ruleset. The existing all-checks gate accepts completed
skipped jobs and waits for running jobs; PRs gain no tag-only wait. Caller
permissions already cover every nested job's request, including `actions: read`
for SARIF, `packages: write`, `id-token: write`, and `attestations: write`.

These are additive probes and smoke gates. Roll back by reverting this change or
deploying the previous image digest; no production migration or backfill is
added.

## Test job time limits

A backend test that blocks forever fails its job after a fixed limit, and the
log shows where it is stuck (#846):

- `test` (every Python version) and `test-coverage`: 20 minutes, through the
  `timeout-minutes` input of the pinned reusable. Slowest observed runs: 268 and
  283 seconds.
- `test-postgres-migrations` and `test-postgres-application`: 30 minutes, as
  job-level `timeout-minutes`. Slowest observed runs: 503 and 625 seconds.

The observed figures are the slowest successful runs among 40 `CI - Tests` runs
before the change. The PostgreSQL maxima include an `Install uv` step that
stalled for about 450 seconds and then succeeded; without that stall the replay
took at most 93 seconds and the application tests at most 234 seconds. The
pinned `reusable-test-python.yml` (v0.98.3) applies its `timeout-minutes` input
(default 60) to each per-version test job; its prepare and aggregate jobs keep
their own 10-minute caps.

`faulthandler_timeout = 300` in `backend/pyproject.toml` makes pytest dump the
stack of every thread to the log when one test runs for more than 300 seconds
(the slowest test takes about 11 seconds). It only prints: the test keeps
running until the job limit stops it. Because it is pytest configuration, it
applies to every pytest run in `backend/`, including both PostgreSQL scripts and
local runs. The dump names the test file and function in its frames, so the
cause is visible without reproducing the hang.

A job stopped by its limit concludes `cancelled`; `evaluate-test-gate.sh` fails
on `cancelled`, so `test-suite / 🧪 Test Suite` fails. There is no per-test
limit and no retry. `backend/tests/test_ci_test_time_limits.py` fails if any of
these limits is removed or `faulthandler_timeout` is not below every one of
them.

## Concurrency for main commits

GitHub keeps at most one running and one pending run per concurrency group. A
newly queued run replaces the pending one even when `cancel-in-progress` is
false, so a group shared by every push to `main` loses a commit's run whenever
three pushes overlap. Until #44 that happened on `main`: the `CI - Tests` run
for `4655557` was cancelled while still pending, with no job started, when
`0d35ec3` was pushed eight minutes later, and the all-checks gate for `4655557`
failed on that cancelled run. The pinned infra-failure re-run workflow re-runs
failed runs only, so such a commit never got its result.

Every workflow file belongs to one class, checked by
`tests/bats/ci/test_main_validation_concurrency.bats`:

| Class              | Workflows                                                                                                                    | Concurrency                                                                                          |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Main validation    | `all-checks-gate`, `codeql`, `docker-ci`, `frontend-ci`, `pin-sync-guard`, `quality-ci`, `sbom`, `security-audit`, `test-ci` | Push to `main`: one group per commit, never cancelled. Everything else: the group without the suffix |
| Release mutations  | `release-auto-tag`, `release-version-pr`                                                                                     | One repository-wide group, no cancel-in-progress (unchanged)                                         |
| Scheduled upkeep   | `ghcr-cleanup`, `scorecards`, `vuln-suppression-check`                                                                       | Unchanged                                                                                            |
| Pull request only  | `ai-review`, `dependency-review`, `pr-auto-assign`, `pr-labeler`, `semantic-pr-title`, `validate-action-pinning`             | Unchanged                                                                                            |
| Follows other runs | `auto-rerun-on-infra-failure`                                                                                                | None                                                                                                 |

A main validation group appends `-<sha>` only when
`github.event_name == 'push' && github.ref == 'refs/heads/main'`. Pull request
runs keep one group per pull request and still cancel the run they supersede.
Merge queue entries keep their per-entry ref. Tag pushes of `docker-ci` and
`sbom` keep one group per tag, never cancelled. Manual and scheduled runs on
`main` keep the shared `main` group, and they no longer share a group with
pushes. The all-checks gate passes no `head-sha`, so each gate run judges
`github.sha`, the commit its group belongs to.

The test renders each group and `cancel-in-progress` value with GitHub's
expression semantics (`tests/bats/ci/helpers/github_expression.py`) for concrete
events. It then replays those runs through the queueing rule above. It also
fails when a workflow file is not classified, when `on` is not a mapping, list
or event name, when a main validation job has a `concurrency` key of its own,
and when a workflow outside `ai-review`, `pr-auto-assign`, `pr-labeler` and
`auto-rerun-on-infra-failure` loses its block.

Anything the helper does not model fails the test instead of producing a value.
Every group and `cancel-in-progress` value is evaluated for each event that
starts the workflow. The context defines only `github.event_name`, `ref`, `sha`,
`repository`, `workflow` and `event.pull_request` (null without a pull request),
so any other context property that an evaluated operand reads fails the test
with its name. Other expressions with no single GitHub result also fail: an
object used as a string, a malformed `format` string, a number that is not an
integer below 1e15, and non-ASCII case deciding a comparison.

Whether a push to `main` runs a workflow is read from its `push` filters, which
the test compares only as literal branch names. A `branches` or
`branches-ignore` entry that GitHub reads as a pattern (`*`, `**`, `?`, `+`,
`[...]`, a leading `!`, a `\` escape) fails the test, which prints the filter.
Decide from GitHub's filter pattern rules whether a push to `main` runs the
workflow and record it in `PUSH_FILTERS_BY_HAND` with that filter. The entry
applies only to an identical filter, so it stops applying when the filter
changes. A `false` entry can never make the test pass, because only validation
and release workflows may have a `push` trigger and they must run on `main`. A
filter that is not a list, an empty list, an empty entry, `branches` with only
negated patterns, or `branches` together with `branches-ignore` fails without
the hand list: fix the filter.

The test reads only this repository's files. When the lgtm-ci pin is bumped,
check the called reusable workflows for `concurrency` blocks keyed on the ref
with cancellation, because such a block would cancel main runs again and the
test cannot see it.

### What is not preserved

`release-version-pr` and `release-auto-tag` keep their repository-wide groups.
With three pushes to `main` inside one release run, the middle commit's pending
release runs are still replaced by the newest ones. The all-checks gate judges
every workflow run on its commit and fails on a cancelled one, so that commit's
gate run fails even though its validation runs passed. A replaced auto-tag run
for a release commit never cuts that commit's tag. This is tracked on #45 and
not changed here.

Seven main validation workflows (`codeql`, `docker-ci`, `frontend-ci`,
`quality-ci`, `sbom`, `security-audit`, `test-ci`) call the pinned main failure
notifier, which keeps one open issue per workflow key and branch. The shared
`main` group used to serialize those calls. Now two failing commits of the same
workflow can run the notifier at once, so two issues for one workflow key are
possible. The reusable has no `concurrency` of its own; the fix belongs in the
reusable notifier.

Code-scanning uploads (`codeql`, and the image scan in `docker-ci`) for two main
commits can finish out of order. Each upload names its commit, but which one
GitHub treats as current for `main` when an older commit's upload arrives last
is not established; alert state may lag one commit until the next push.

### Post-merge check

A pull request cannot show two pushes to `main` both completing. After merge,
read the run history without triggering anything. Cancelled validation runs:

```sh
gh run list --branch main --event push --limit 200 \
  --json headSha,name,conclusion,createdAt |
  jq -r '.[] | select(.conclusion == "cancelled")
    | select(.name | startswith("Release - ") | not)
    | [.createdAt, .name, .headSha[0:7]] | @tsv'
```

No validation run created after the merge should appear. Per-commit
completeness, as one line per commit (first run time, commit, successful
validation workflows, missing ones):

```sh
gh run list --branch main --event push --limit 200 \
  --json headSha,name,conclusion,createdAt |
  jq -r '
    ["Quality - Lint & Format", "CI - Tests", "CI - Frontend",
     "Security - CodeQL Code Scanning", "Security - OSV Audit",
     "CI - Docker Build & Publish", "Security - SBOM Generation",
     "PR - All Checks Gate"] as $expected
    | group_by(.headSha)[]
    | (map(select(.conclusion == "success") | .name) | unique) as $ok
    | ($expected - $ok) as $missing
    | [(map(.createdAt) | min), .[0].headSha[0:7],
       ($expected | length) - ($missing | length),
       ($missing | join(", "))]
    | @tsv' | sort
```

Expect `8` with nothing missing for every commit pushed after the merge.
`Quality - Pin Sync Guard` is not counted: its path filter runs it only when
`.github/workflows/**` or the guard's script or yq program changed, and then it
should also succeed. No other validation workflow has a path filter. Runs still
in progress show as missing, and the oldest commit in the 200-run window can be
cut off. A commit whose only gap is `PR - All Checks Gate` while a release run
of the same commit was cancelled or failed is a release problem (above), not a
lost validation run.
