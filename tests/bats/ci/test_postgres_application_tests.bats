#!/usr/bin/env bats
# Tests for .github/scripts/postgres-application-tests.sh (#724)

setup() {
  PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/../../.." && pwd)"
  SCRIPT="${PROJECT_ROOT}/.github/scripts/postgres-application-tests.sh"
  # uv stub: records each invocation; `uv run` exits with UV_RUN_EXIT.
  STUB_BIN="${BATS_TEST_TMPDIR}/bin"
  export UV_LOG="${BATS_TEST_TMPDIR}/uv.log"
  mkdir -p "${STUB_BIN}"
  cat >"${STUB_BIN}/uv" <<'EOF'
#!/usr/bin/env bash
echo "$*" >>"${UV_LOG}"
if [[ "$1" == "run" ]]; then
  exit "${UV_RUN_EXIT:-0}"
fi
EOF
  chmod +x "${STUB_BIN}/uv"
  export PATH="${STUB_BIN}:${PATH}"
  export PODEX_DATABASE__URL="postgresql+psycopg://podex@localhost:5432/podex"
}

@test "fails before installing when PODEX_DATABASE__URL is unset" {
  unset PODEX_DATABASE__URL
  run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"PODEX_DATABASE__URL must be set"* ]]
  [ ! -e "${UV_LOG}" ]
}

@test "fails before installing when PODEX_DATABASE__URL is not PostgreSQL" {
  PODEX_DATABASE__URL="sqlite:///./podex.db" run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"must be a postgresql URL"* ]]
  [ ! -e "${UV_LOG}" ]
}

@test "runs only postgres-marked tests with PostgreSQL required" {
  run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  grep -qx "sync" "${UV_LOG}"
  grep -qx "run pytest tests -m postgres --require-postgres -ra" "${UV_LOG}"
}

@test "propagates a failing test run" {
  UV_RUN_EXIT=1 run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
}

@test "propagates pytest's no-tests-collected exit code" {
  UV_RUN_EXIT=5 run bash "${SCRIPT}"
  [ "${status}" -eq 5 ]
}
