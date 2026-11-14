#!/usr/bin/env bash
# Start the production command, wait for Docker health, and exercise a real route.
set -euo pipefail

# Bound Docker calls as well as HTTP waits. TERM reaches the cleanup trap.
if [[ "${PODEX_SMOKE_BOUNDED:-0}" != 1 ]]; then
	exec timeout --signal=TERM --kill-after=15s 180s \
		env PODEX_SMOKE_BOUNDED=1 bash "$0" "$@"
fi

: "${IMAGE:?IMAGE must name the built image (a digest on publish)}"
kind="${1:?Expected backend or frontend}"
case "$kind" in backend | frontend) ;; *) exit 2 ;; esac
script_dir="$(cd "$(dirname "$0")" && pwd)"
container="podex-smoke-${kind}-${BASHPID}-${RANDOM}"
bad_migration="${container}-migration-failure"
scheduler_exec="${container}-scheduler-exec"
scheduler_shell="${container}-scheduler-shell"
scheduler_configured="${container}-scheduler-configured"
api_decoy="${container}-api-decoy"
shell_decoy="${container}-shell-decoy"
platform_args=()
if [[ -n "${PLATFORM:-}" ]]; then
	platform_args=(--platform "$PLATFORM")
fi

cleanup() {
	local result=$?
	trap - EXIT
	for candidate in "$container" "$bad_migration" "$scheduler_exec" \
		"$scheduler_shell" "$scheduler_configured" "$api_decoy" "$shell_decoy"; do
		if docker container inspect "$candidate" >/dev/null 2>&1; then
			if ! docker rm -f "$candidate" >/dev/null; then
				echo "Container cleanup failed" >&2
				result=1
			fi
		fi
	done
	exit "$result"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# Wait until Docker reports the expected health status for a running container.
wait_health() {
	local name="$1" label="$2" expected="${3:-healthy}" state attempt
	for ((attempt = 0; attempt < 60; attempt++)); do
		state=$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$name")
		case "$state" in
		"running ${expected}")
			echo "${label}: Docker health is ${expected}"
			return 0
			;;
		'running starting') sleep 1 ;;
		'running healthy' | 'running unhealthy')
			echo "${label}: Docker health is ${state#running }, expected ${expected}" >&2
			return 1
			;;
		*)
			echo "${label}: unexpected container state: ${state}" >&2
			return 1
			;;
		esac
	done
	echo "${label}: health timeout" >&2
	return 1
}

wait_healthy() {
	wait_health "$container" "$kind"
}

# Start a scheduler container the way the API case starts: a new tmpfs SQLite
# database, AUTO_MIGRATE through the production entrypoint, no network.
# Arguments: name, extra `docker run` options as one word (may be empty),
# then the command.
run_scheduler() {
	local name="$1" options="$2"
	local -a extra=()
	shift 2
	if [[ -n "$options" ]]; then
		extra=("$options")
	fi
	docker run -d --name "$name" "${platform_args[@]}" --network none \
		--tmpfs /data:rw,uid=1001,gid=1001 \
		-e AUTO_MIGRATE=1 -e PODEX_DATABASE__URL=sqlite:////data/smoke.db \
		"${extra[@]}" "$IMAGE" "$@" >/dev/null
}

# Start an API-shaped container that never listens on port 8000. Health timing
# is overridden so one failed probe after the image's 5-second start interval
# reports unhealthy instead of waiting out the 300-second start period.
run_decoy() {
	local name="$1"
	shift
	docker run -d --name "$name" "${platform_args[@]}" --network none \
		--tmpfs /data:rw,uid=1001,gid=1001 \
		-e PODEX_DATABASE__URL=sqlite:////data/smoke.db \
		--health-start-period=1s --health-interval=2s --health-retries=1 \
		"$IMAGE" "$@" >/dev/null
}

# A decoy must be unhealthy through clean HTTP failures (exit 1, no output:
# the HTTP check ran, rather than the probe crashing or rejecting a role),
# and its scheduler processes must still run, so the probe saw them.
assert_decoy() {
	local name="$1" label="$2" minimum="$3" probe_log clean_failures='^(1:"" )+$'
	wait_health "$name" "$label" unhealthy
	probe_log=$(docker inspect --format \
		'{{range .State.Health.Log}}{{.ExitCode}}:{{printf "%q" .Output}} {{end}}' "$name")
	if [[ ! "$probe_log" =~ $clean_failures ]]; then
		echo "${label}: probe log is not clean HTTP failures: ${probe_log}" >&2
		return 1
	fi
	if ! docker exec "$name" python -c '
import os, pathlib, sys
schedulers = 0
for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
    if path.parent.name in {"1", str(os.getpid())}:
        continue
    try:
        command = path.read_bytes()
    except OSError:
        continue
    schedulers += b"-m\0podex.scheduler_runner\0" in command
sys.exit(0 if schedulers >= int(sys.argv[1]) else 1)
' "$minimum"; then
		echo "${label}: lost its scheduler processes before the verdict" >&2
		return 1
	fi
	echo "${label}: ${minimum} visible scheduler process(es) did not exempt the HTTP check"
}

# Within 20 seconds, PID 1 must be the given program (the entrypoint has
# exec'd) and a scheduler module process must be running.
assert_scheduler_process() {
	local name="$1" label="$2" pid1_program="$3"
	if ! docker exec "$name" python -c '
import pathlib, sys, time
expected = sys.argv[1]
for _ in range(40):
    pid1 = pathlib.Path("/proc/1/cmdline").read_bytes().split(b"\0")
    running = []
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            running.append(path.read_bytes())
        except OSError:
            continue
    if pid1[0].rsplit(b"/", 1)[-1] == expected.encode() and any(
        b"-m\0podex.scheduler_runner\0" in command for command in running
    ):
        sys.exit(0)
    time.sleep(0.5)
sys.exit(1)
' "$pid1_program"; then
		echo "${label}: PID 1 is not ${pid1_program} with a running scheduler" >&2
		return 1
	fi
	echo "${label}: PID 1 is ${pid1_program} and the scheduler module is running"
}

if [[ "$kind" == backend ]]; then
	# A new writable SQLite file exercises the supported default database and
	# the production AUTO_MIGRATE entrypoint, without an external service.
	docker run -d --name "$container" "${platform_args[@]}" --network none \
		--tmpfs /data:rw,uid=1001,gid=1001 \
		-e AUTO_MIGRATE=1 -e PODEX_DATABASE__URL=sqlite:////data/smoke.db "$IMAGE"
	# The scheduler has no HTTP listener. Exec form and a single-command
	# `sh -c` are recognised from PID 1 without configuration; a compound
	# script is healthy only through the explicit role setting.
	run_scheduler "$scheduler_exec" '' python -m podex.scheduler_runner
	run_scheduler "$scheduler_shell" '' sh -c 'exec python -u -m podex.scheduler_runner'
	run_scheduler "$scheduler_configured" --env=PODEX_PROCESS_ROLE=scheduler \
		sh -c 'python -u -m podex.scheduler_runner & wait'
	# Negative cases. PID 1 stays alive without listening, a scheduler it
	# started runs beside it, and another is started with docker exec; and
	# the compound shell form above without the role setting. Neither may
	# exempt the API check.
	run_decoy "$api_decoy" python -c '
import subprocess, sys, time
subprocess.Popen([sys.executable, "-m", "podex.scheduler_runner"])
time.sleep(600)
'
	docker exec -d "$api_decoy" python -m podex.scheduler_runner
	run_decoy "$shell_decoy" sh -c 'python -u -m podex.scheduler_runner & wait'
	wait_healthy
	docker exec "$container" python -c '
import json
import urllib.request
for path in ("/health", "/api/v2/podcasts"):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=3) as response:
        assert response.status == 200
        body = json.load(response)
        if path == "/health":
            assert body == {"status": "ok"}
        else:
            assert body["items"] == [] and body["total"] == 0
print("backend: health and migrated catalog return HTTP 200 with expected bodies")
'
	# An inaccessible database must fail migrations and prevent Uvicorn startup.
	docker run -d --name "$bad_migration" "${platform_args[@]}" --network none \
		-e AUTO_MIGRATE=1 -e PODEX_DATABASE__URL=sqlite:////proc/podex-smoke.db "$IMAGE"
	exit_code=$(docker wait "$bad_migration")
	if [[ "$exit_code" == 0 ]]; then
		echo 'backend: migration against an inaccessible database succeeded' >&2
		exit 1
	fi
	migration_logs=$(docker logs "$bad_migration" 2>&1)
	if [[ "$migration_logs" != *'AUTO_MIGRATE enabled: running alembic upgrade head'* ]]; then
		echo 'backend: migration failure log is missing the AUTO_MIGRATE line' >&2
		exit 1
	fi
	# Database errors are logged by class and SQLite error name only; the
	# driver's own message (which can carry row values) must not appear.
	if [[ "$migration_logs" != *'OperationalError (OperationalError, SQLITE_CANTOPEN) [driver message withheld]'* ]]; then
		echo 'backend: migration failure log is missing the database error descriptor' >&2
		exit 1
	fi
	if [[ "$migration_logs" == *'unable to open database file'* ]]; then
		echo 'backend: migration failure log contains raw database driver text' >&2
		exit 1
	fi
	echo 'backend: failed migration prevents application startup'
	wait_health "$scheduler_exec" 'backend scheduler (exec form)'
	assert_scheduler_process "$scheduler_exec" 'backend scheduler (exec form)' python
	wait_health "$scheduler_shell" "backend scheduler (sh -c 'exec ...')"
	assert_scheduler_process "$scheduler_shell" "backend scheduler (sh -c 'exec ...')" python
	wait_health "$scheduler_configured" 'backend scheduler (compound sh -c, role set)'
	assert_scheduler_process "$scheduler_configured" \
		'backend scheduler (compound sh -c, role set)' sh
	assert_decoy "$api_decoy" 'backend API with visible schedulers' 2
	assert_decoy "$shell_decoy" 'backend compound sh -c without role' 1
else
	# Keep the baked-in localhost API origin. The stub shares this container's
	# loopback namespace; --network none forbids external backend/telemetry calls.
	docker run -d --name "$container" "${platform_args[@]}" --network none "$IMAGE"
	docker cp "$script_dir/frontend-smoke-stub.mjs" "$container:/tmp/podex-smoke-stub.mjs"
	docker cp "$script_dir/../../frontend/src/test/fixtures/container-smoke-catalog.json" \
		"$container:/tmp/frontend-smoke-catalog.json"
	docker exec -d "$container" node /tmp/podex-smoke-stub.mjs
	wait_healthy
	docker exec "$container" node --input-type=module -e '
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const catalog = JSON.parse(await readFile("/tmp/frontend-smoke-catalog.json", "utf8"));
const health = await fetch("http://127.0.0.1:4321/health", {signal: AbortSignal.timeout(3000)});
assert.equal(health.status, 200);
assert.deepEqual(await health.json(), {status: "ok"});
const page = await fetch("http://127.0.0.1:4321/", {signal: AbortSignal.timeout(5000)});
assert.equal(page.status, 200);
assert.ok((await page.text()).includes(catalog.items[0].name), "SSR must render the stub podcast card");
console.log("frontend: health and SSR catalog return HTTP 200 with expected bodies");
'
fi
