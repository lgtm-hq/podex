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
platform_args=()
if [[ -n "${PLATFORM:-}" ]]; then
	platform_args=(--platform "$PLATFORM")
fi

cleanup() {
	local result=$?
	trap - EXIT
	for candidate in "$container" "$bad_migration"; do
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

wait_healthy() {
	local state attempt
	for ((attempt = 0; attempt < 60; attempt++)); do
		state=$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$container")
		case "$state" in
		'running healthy')
			echo "${kind}: Docker health is healthy"
			return 0
			;;
		'running starting') sleep 1 ;;
		*)
			echo "${kind}: unexpected container state: ${state}" >&2
			return 1
			;;
		esac
	done
	echo "${kind}: health timeout" >&2
	return 1
}

if [[ "$kind" == backend ]]; then
	# A new writable SQLite file exercises the supported default database and
	# the production AUTO_MIGRATE entrypoint, without an external service.
	docker run -d --name "$container" "${platform_args[@]}" --network none \
		--tmpfs /data:rw,uid=1001,gid=1001 \
		-e AUTO_MIGRATE=1 -e PODEX_DATABASE__URL=sqlite:////data/smoke.db "$IMAGE"
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
	[[ "$exit_code" != 0 ]]
	migration_logs=$(docker logs "$bad_migration" 2>&1)
	[[ "$migration_logs" == *'AUTO_MIGRATE enabled: running alembic upgrade head'* ]]
	[[ "$migration_logs" == *'unable to open database file'* ]]
	echo 'backend: failed migration prevents application startup'
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
