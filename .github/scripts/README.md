# Container smoke tests

Run from any working directory with Docker and GNU `timeout` available:

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
without credentials or extra packages. The interval is 30 seconds, request
timeout 2 seconds, Docker timeout 5 seconds, and three consecutive failures mark
the container unhealthy. Backend startup has a 60-second grace period for
optional migrations; frontend has 30 seconds. Once startup grace expires,
failure is detected within about 95 seconds. Responses need HTTP 200.

Backend `/health` is HTTP liveness and is exempt from rate limiting at that
exact path. It does **not** query the database. Frontend `/health` is uncached
SSR HTTP liveness and does **not** fetch the backend. These probes distinguish
serving HTTP from a process that is alive but stalled; they do not prove
dependency readiness. The backend scheduler command
(`python -m podex.scheduler_runner`) shares the image but has no HTTP listener.
Its probe recognizes PID 1's module command and returns success without HTTP;
this does not prove scheduler progress or database readiness. Other command
overrides retain the API probe.

Backend smoke starts from a writable, empty SQLite database in tmpfs, enables
`AUTO_MIGRATE=1`, then reads `/api/v2/podcasts` and asserts an empty page. This
tests the supported default database, Alembic chain, entrypoint, routing and ORM
read; it does not test PostgreSQL or external providers. A second container uses
an inaccessible SQLite path and must exit nonzero with a migration failure
before the server starts. Production auto-migration remains opt-in.

Frontend smoke starts a Node catalog stub on loopback port 8000 inside the same
network-disabled container, preserving the image's build-time API origin. It
checks `/health` and `/` for HTTP 200 and the empty catalog rendering. A backend
fetch failure renders a different banner, so an HTTP 200 error fallback cannot
pass this assertion. This proves SSR integration with the catalog response
shape, not compatibility with a deployed backend or a browser's public API
origin. The podex-ops frontend template currently probes `/`; it is unchanged
here.

## CI contract at lgtm-ci v0.98.3

The pinned reusable's `health-check-cmd` executes on the runner with `IMAGE`
exported. It starts a default-command container, waits for the published
localhost port, then executes the command under `health-check-timeout`. The
command invokes our complete smoke script, which owns migration settings and
network isolation. Single-platform PR, merge-group, main push and manual builds
exercise this path on linux/amd64. Smoke scripts resolve from the triggering
application checkout; tooling resolves from the workflow SHA without a caller
`tooling-ref`.

`smoke-test-script` is forwarded only to the split path. Tag publishes request
linux/amd64 and linux/arm64; verify jobs pull each staging image by immutable
digest and invoke the smoke script with `IMAGE`, `PLATFORM`, and `REGISTRY`. The
health hook also runs per platform. Both block manifest merging on failure. The
standalone smoke reusable requires a published digest; it cannot validate a
PR-local image. The split reusable already tests the staging digests, so no
additional post-publish jobs are needed. The main failure notifier continues to
depend on both parent image jobs, which include their smoke gates. It does not
run for tag failures.

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

On tags the existing split verification and health job base names become active
for each linux/amd64 and linux/arm64 matrix entry (GitHub appends matrix
suffixes):

```text
🐳 Backend Image / Docker multi-platform / Publish / Docker verify per platform
🐳 Backend Image / Docker multi-platform / Publish / Docker health check per platform
🎨 Frontend Image / Docker multi-platform / Publish / Docker verify per platform
🎨 Frontend Image / Docker multi-platform / Publish / Docker health check per platform
```

No check is added to a ruleset. The existing all-checks gate accepts completed
skipped jobs and waits for running jobs; PRs gain no tag-only wait. Caller
permissions already cover every nested job's request, including `actions: read`
for SARIF, `packages: write`, `id-token: write`, and `attestations: write`.

These are additive probes and smoke gates. Roll back by reverting this change or
deploying the previous image digest; no production migration or backfill is
added.
