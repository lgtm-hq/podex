#!/usr/bin/env bats
# Tests for .github/scripts/evaluate-test-gate.sh (#550)

setup() {
  PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/../../.." && pwd)"
  SCRIPT="${PROJECT_ROOT}/.github/scripts/evaluate-test-gate.sh"
  export TEST_RESULT=success
  export COVERAGE_RESULT=success
  export MIGRATIONS_RESULT=success
  export SHELL_RESULT=success
  export GITHUB_OUTPUT="${BATS_TEST_TMPDIR}/github_output"
  : >"${GITHUB_OUTPUT}"
}

assert_outputs() {
  local result="$1"
  local passed="$2"
  grep -qx "result=${result}" "${GITHUB_OUTPUT}"
  grep -qx "passed=${passed}" "${GITHUB_OUTPUT}"
}

@test "passes when every upstream job succeeds" {
  run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"All upstream test jobs passed or were skipped"* ]]
  assert_outputs success true
}

@test "passes when every upstream job is skipped (draft PR)" {
  TEST_RESULT=skipped COVERAGE_RESULT=skipped MIGRATIONS_RESULT=skipped \
    SHELL_RESULT=skipped run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  assert_outputs success true
}

@test "passes on a mix of success and skipped" {
  MIGRATIONS_RESULT=skipped run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  assert_outputs success true
}

@test "fails when any single upstream job fails" {
  local var
  for var in TEST_RESULT COVERAGE_RESULT MIGRATIONS_RESULT SHELL_RESULT; do
    : >"${GITHUB_OUTPUT}"
    run env "${var}=failure" bash "${SCRIPT}"
    [ "${status}" -eq 1 ]
    [[ "${output}" == *"::error::Upstream test jobs failed:"*"(failure)"* ]]
    assert_outputs failure false
  done
}

@test "fails when any single upstream job is cancelled" {
  local var
  for var in TEST_RESULT COVERAGE_RESULT MIGRATIONS_RESULT SHELL_RESULT; do
    : >"${GITHUB_OUTPUT}"
    run env "${var}=cancelled" bash "${SCRIPT}"
    [ "${status}" -eq 1 ]
    [[ "${output}" == *"(cancelled)"* ]]
    assert_outputs failure false
  done
}

@test "names every failing job in the error" {
  TEST_RESULT=failure MIGRATIONS_RESULT=cancelled run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"test (failure)"* ]]
  [[ "${output}" == *"test-postgres-migrations (cancelled)"* ]]
  [[ "${output}" != *"test-coverage ("* ]]
}

@test "fails closed on an empty result" {
  COVERAGE_RESULT="" run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"test-coverage (unexpected result: '')"* ]]
  assert_outputs failure false
}

@test "fails closed on an unknown result" {
  TEST_RESULT=neutral run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"unexpected result: 'neutral'"* ]]
}

@test "fails when a required variable is unset" {
  unset SHELL_RESULT
  run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"SHELL_RESULT is not set"* ]]
  assert_outputs failure false
}

@test "skips output writes when GITHUB_OUTPUT is unset" {
  unset GITHUB_OUTPUT
  run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
}

@test "--help prints usage and exits 0" {
  run bash "${SCRIPT}" --help
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Usage:"* ]]
  [[ "${output}" == *"MIGRATIONS_RESULT"* ]]
}

@test "rejects unexpected arguments" {
  run bash "${SCRIPT}" --bogus
  [ "${status}" -eq 2 ]
}
