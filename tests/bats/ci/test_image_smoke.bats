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
case "$1" in
run)
  touch "$SMOKE_STATE/created"
  [[ "$SCENARIO" != bad-command ]] || exit 127
  echo fake-container
  ;;
inspect)
  case "$SCENARIO" in
  exited) echo 'exited starting' ;;
  timeout) echo 'running starting' ;;
  *) echo 'running healthy' ;;
  esac
  ;;
container) test -f "$SMOKE_STATE/created" ;;
exec) [[ "$SCENARIO" != http-failure ]] ;;
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
rm) touch "$SMOKE_STATE/removed" ;;
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

@test "backend smoke exercises migrated catalog and checks migration failure" {
  run_smoke
  [ "$status" -eq 0 ]
  [[ "$output" == *'failed migration prevents application startup'* ]]
  [ -f "$SMOKE_STATE/removed" ]
  run grep -F -- '--network none' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
}

@test "frontend smoke starts an isolated stub and removes its container" {
  run_smoke frontend
  [ "$status" -eq 0 ]
  run grep -F -- 'node /tmp/podex-smoke-stub.mjs' "$SMOKE_STATE/commands"
  [ "$status" -eq 0 ]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "bad command fails and cleans up a partially created container" {
  export SCENARIO=bad-command
  run -127 bash "${PROJECT_ROOT}/.github/scripts/smoke-image.sh" backend
  [ "$status" -ne 0 ]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "exited application fails health wait and cleans up" {
  export SCENARIO=exited
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'unexpected container state'* ]]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "HTTP assertion failure fails smoke and cleans up" {
  export SCENARIO=http-failure
  run_smoke
  [ "$status" -ne 0 ]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "application that never becomes healthy times out and cleans up" {
  export SCENARIO=timeout
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'health timeout'* ]]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "migration log with raw driver text fails smoke and cleans up" {
  export SCENARIO=migration-driver-text
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'raw database driver text'* ]]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "migration log without the error descriptor fails smoke and cleans up" {
  export SCENARIO=migration-no-descriptor
  run_smoke
  [ "$status" -ne 0 ]
  [[ "$output" == *'missing the database error descriptor'* ]]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "migration unexpectedly succeeding fails smoke and cleans up" {
  export SCENARIO=migration-succeeded
  run_smoke
  [ "$status" -ne 0 ]
  [ -f "$SMOKE_STATE/removed" ]
}

@test "deadline terminates a stuck Docker call and cleans up" {
  export SCENARIO=hung
  run -124 timeout --signal=TERM --kill-after=5s 1s \
    env PODEX_SMOKE_BOUNDED=1 \
    bash "${PROJECT_ROOT}/.github/scripts/smoke-image.sh" backend
  [ "$status" -eq 124 ]
  [ -f "$SMOKE_STATE/removed" ]
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
