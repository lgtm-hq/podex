#!/usr/bin/env bats
# Smoke orchestration failures must propagate and containers must be removed.
bats_require_minimum_version 1.5.0

setup() {
  PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/../../.." && pwd)"
  export SMOKE_STATE="${BATS_TEST_TMPDIR}/state"
  mkdir -p "${SMOKE_STATE}" "${BATS_TEST_TMPDIR}/bin"
  export IMAGE=podex-test:local
  export PATH="${BATS_TEST_TMPDIR}/bin:${PATH}"
  export SCENARIO=healthy
  cat > "${BATS_TEST_TMPDIR}/bin/docker" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$SMOKE_STATE/commands"
name="${!#}"
case "$1" in
run)
  previous=
  for argument in "$@"; do
    if [[ "$previous" == --name ]]; then
      printf '%s\n' "$argument" >> "$SMOKE_STATE/started"
    fi
    previous="$argument"
  done
  [[ "$SCENARIO" != bad-command ]] || exit 127
  echo fake-container
  ;;
inspect)
  if [[ "$*" == *'.State.Health.Log'* ]]; then
    if [[ "$SCENARIO" == decoy-probe-crashed ]]; then
      echo '1:"" 1:"Traceback (most recent call last):\n"'
    else
      echo '1:"" 1:"" '
    fi
    exit 0
  fi
  case "$SCENARIO:$name" in
  exited:*) echo 'exited starting' ;;
  timeout:*) echo 'running starting' ;;
  decoy-healthy:*-api-decoy) echo 'running healthy' ;;
  *:*-api-decoy) echo 'running unhealthy' ;;
  scheduler-unhealthy:*-scheduler-shell) echo 'running unhealthy' ;;
  *) echo 'running healthy' ;;
  esac
  ;;
container) grep -qxF -- "$name" "$SMOKE_STATE/started" 2>/dev/null ;;
exec)
  case "$SCENARIO" in
  http-failure) [[ "$*" != *urllib.request* ]] ;;
  scheduler-process-missing) [[ "$*" != *'-scheduler-exec python -c'* ]] ;;
  decoy-processes-gone) [[ "$*" != *'-api-decoy python -c'* ]] ;;
  esac
  ;;
wait)
  if [[ "$SCENARIO" == hung ]]; then /bin/sleep 30; fi
  if [[ "$SCENARIO" == migration-succeeded ]]; then echo 0; else echo 1; fi
  ;;
logs)
  echo 'AUTO_MIGRATE enabled: running alembic upgrade head'
  case "$SCENARIO" in
  migration-driver-text)
    echo 'sqlalchemy.exc.OperationalError: OperationalError (OperationalError, SQLITE_CANTOPEN) [driver message withheld]'
    echo 'sqlite3.OperationalError: unable to open database file'
    ;;
  migration-no-descriptor) echo 'Traceback (most recent call last):' ;;
  *) echo 'sqlalchemy.exc.OperationalError: OperationalError (OperationalError, SQLITE_CANTOPEN) [driver message withheld]' ;;
  esac
  ;;
rm)
  printf '%s\n' "$name" >> "$SMOKE_STATE/removed"
  ;;
cp) ;;
*) exit 2 ;;
esac
MOCK
  cat > "${BATS_TEST_TMPDIR}/bin/sleep" <<'MOCK'
#!/usr/bin/env bash
exit 0
MOCK
  chmod +x "${BATS_TEST_TMPDIR}/bin/"*
}

run_smoke() {
  run bash "${PROJECT_ROOT}/.github/scripts/smoke-image.sh" "${1:-backend}"
}

# Every container the smoke named in `docker run` was removed again.
all_started_removed() {
  [ -s "$SMOKE_STATE/started" ]
  [ -f "$SMOKE_STATE/removed" ]
  [ -z "$(sort -u "$SMOKE_STATE/removed" | comm -23 <(sort -u "$SMOKE_STATE/started") -)" ]
}

# Every `docker run` is network-isolated and publishes no host ports.
runs_are_isolated() {
  local line runs=0
  while IFS= read -r line; do
    [[ "$line" == run\ * ]] || continue
    runs=$((runs + 1))
    [[ "$line" == *' --network none '* ]]
    [[ "$line" != *' -p '* && "$line" != *'--publish'* ]]
  done < "$SMOKE_STATE/commands"
  [ "$runs" -gt 0 ]
}

@test "backend smoke exercises migrated catalog and checks migration failure" {
  run_smoke
  [ "$status" -eq 0 ]
  [[ "$output" == *'failed migration prevents application startup'* ]]
  all_started_removed
  runs_are_isolated
}

@test "backend smoke proves scheduler start forms healthy and a decoy API unhealthy" {
  run_smoke
  [ "$status" -eq 0 ]
  [[ "$output" == *'backend scheduler (exec form): Docker health is healthy'* ]]
  [[ "$output" == *'backend scheduler (exec form): PID 1 is python and the scheduler module is running'* ]]
  [[ "$output" == *'backend scheduler (sh -c form): Docker health is healthy'* ]]
  [[ "$output" == *'backend scheduler (sh -c form): PID 1 is sh and the scheduler module is running'* ]]
  [[ "$output" == *'backend API with visible schedulers: Docker health is unhealthy'* ]]
  [[ "$output" == *'API with two visible scheduler processes still fails its HTTP check'* ]]
  [ "$(wc -l < "$SMOKE_STATE/started")" -eq 5 ]
  [ "$(sort -u "$SMOKE_STATE/started" | wc -l)" -eq 5 ]
  # Scheduler containers migrate a fresh tmpfs database through the entrypoint.
  run grep -cE -- '^run .*-scheduler-(exec|shell) .*--tmpfs /data:rw,uid=1001,gid=1001 -e AUTO_MIGRATE=1 -e PODEX_DATABASE__URL=sqlite:////data/smoke.db podex-test:local ' "$SMOKE_STATE/commands"
  [ "$output" = 2 ]
  run grep -E -- '-scheduler-exec .* podex-test:local python -m podex.scheduler_runner$' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  run grep -E -- '-scheduler-shell .* podex-test:local sh -c python -u -m podex.scheduler_runner & wait$' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  # The decoy overrides only health timing, so one failed probe after the
  # image's start interval reports unhealthy well inside the deadline.
  run grep -E -- '-api-decoy .*--health-start-period=1s --health-interval=2s --health-retries=1 podex-test:local python -c' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  run grep -E -- '-api-decoy .*AUTO_MIGRATE' "$SMOKE_STATE/commands"
  [ "$status" -ne 0 ]
  run grep -E -- '^exec -d podex-smoke-backend-.*-api-decoy python -m podex.scheduler_runner$' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  all_started_removed
  runs_are_isolated
}

@test "digest image with a platform reaches every container start" {
  export IMAGE='ghcr.io/lgtm-hq/podex-backend@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
  export PLATFORM=linux/arm64
  run_smoke
  [ "$status" -eq 0 ]
  run grep -c '^run ' "$SMOKE_STATE/commands"
  [ "$output" = 5 ]
  run grep -cF -- "--platform linux/arm64 --network none" "$SMOKE_STATE/commands"
  [ "$output" = 5 ]
  run grep -cF -- " ${IMAGE}" "$SMOKE_STATE/commands"
  [ "$output" = 5 ]
  all_started_removed
}

@test "local tag without a platform passes no platform flag" {
  run_smoke
  [ "$status" -eq 0 ]
  run grep -F -- '--platform' "$SMOKE_STATE/commands"
  [ "$status" -ne 0 ]
}

@test "decoy API reported healthy fails smoke and cleans up" {
  export SCENARIO=decoy-healthy
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'backend API with visible schedulers: Docker health is healthy, expected unhealthy'* ]]
  all_started_removed
}

@test "unhealthy scheduler container fails smoke and cleans up" {
  export SCENARIO=scheduler-unhealthy
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'backend scheduler (sh -c form): Docker health is unhealthy, expected healthy'* ]]
  all_started_removed
}

@test "scheduler not running as PID 1 fails smoke and cleans up" {
  export SCENARIO=scheduler-process-missing
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'backend scheduler (exec form): PID 1 is not python with a running scheduler'* ]]
  all_started_removed
}

@test "decoy probe output other than clean HTTP failure fails smoke" {
  export SCENARIO=decoy-probe-crashed
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'API decoy probe log is not clean HTTP failures'* ]]
  all_started_removed
}

@test "decoy without visible scheduler processes fails smoke" {
  export SCENARIO=decoy-processes-gone
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'API decoy lost its scheduler processes before the verdict'* ]]
  all_started_removed
}

@test "frontend smoke starts an isolated stub and removes its container" {
  run_smoke frontend
  [ "$status" -eq 0 ]
  run grep -F -- 'node /tmp/podex-smoke-stub.mjs' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  all_started_removed
}

@test "bad command fails and cleans up a partially created container" {
  export SCENARIO=bad-command
  run -127 bash "${PROJECT_ROOT}/.github/scripts/smoke-image.sh" backend
  [ "$status" -ne 0 ]
  all_started_removed
}

@test "exited application fails health wait and cleans up" {
  export SCENARIO=exited
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'unexpected container state'* ]]
  all_started_removed
}

@test "HTTP assertion failure fails smoke and cleans up" {
  export SCENARIO=http-failure
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" != *'health and migrated catalog return HTTP 200'* ]]
  all_started_removed
}

@test "application that never becomes healthy times out and cleans up" {
  export SCENARIO=timeout
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'health timeout'* ]]
  all_started_removed
}

@test "migration log with raw driver text fails smoke and cleans up" {
  export SCENARIO=migration-driver-text
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'raw database driver text'* ]]
  all_started_removed
}

@test "migration log without the error descriptor fails smoke and cleans up" {
  export SCENARIO=migration-no-descriptor
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'missing the database error descriptor'* ]]
  all_started_removed
}

@test "migration unexpectedly succeeding fails smoke and cleans up" {
  export SCENARIO=migration-succeeded
  run_smoke
  [ "$status" -ne 0 ]
  all_started_removed
}

@test "deadline terminates a stuck Docker call and cleans up" {
  export SCENARIO=hung
  run -124 timeout --signal=TERM --kill-after=5s 1s \
    env PODEX_SMOKE_BOUNDED=1 \
    bash "${PROJECT_ROOT}/.github/scripts/smoke-image.sh" backend
  [ "$status" -eq 124 ]
  all_started_removed
}

@test "both images use digest smoke on tags and health hooks only off tags" {
  local image port key value expected setting
  for image in backend frontend; do
    if [[ "$image" == backend ]]; then port=8000; else port=4321; fi
    for setting in \
      "health-check-cmd|bash .github/scripts/smoke-${image}.sh" \
      "health-check-port|${port}" \
      "health-check-timeout|210s"; do
      IFS='|' read -r key value <<<"$setting"
      run yq -r ".jobs.${image}.with.\"${key}\"" \
        "${PROJECT_ROOT}/.github/workflows/docker-ci.yml"
      [ "$status" -eq 0 ]
      expected="\${{ !startsWith(github.ref, 'refs/tags/v') && '${value}' || '' }}"
      [ "$output" = "$expected" ]
    done
    run yq -r ".jobs.${image}.with.smoke-test-script" \
      "${PROJECT_ROOT}/.github/workflows/docker-ci.yml"
    [ "$status" -eq 0 ]
    [ "$output" = ".github/scripts/smoke-${image}.sh" ]
  done
}
