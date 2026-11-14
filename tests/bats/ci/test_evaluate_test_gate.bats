#!/usr/bin/env bats
# Tests for .github/scripts/evaluate-test-gate.sh (#550)

setup() {
  PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/../../.." && pwd)"
  SCRIPT="${PROJECT_ROOT}/.github/scripts/evaluate-test-gate.sh"
  export IS_DRAFT=false
  export TEST_RESULT=success
  export COVERAGE_RESULT=success
  export MIGRATIONS_RESULT=success
  export POSTGRES_RESULT=success
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
  [[ "${output}" == *"All upstream test jobs passed"* ]]
  assert_outputs success true
}

@test "passes when every upstream job is skipped on a draft PR" {
  IS_DRAFT=true TEST_RESULT=skipped COVERAGE_RESULT=skipped \
    MIGRATIONS_RESULT=skipped POSTGRES_RESULT=skipped SHELL_RESULT=skipped \
    run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"(draft pull request)"* ]]
  assert_outputs success true
}

@test "passes on a mix of success and skipped on a draft PR" {
  IS_DRAFT=true MIGRATIONS_RESULT=skipped POSTGRES_RESULT=skipped \
    run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  assert_outputs success true
}

@test "fails when any single upstream job is skipped on a non-draft run" {
  local var
  for var in TEST_RESULT COVERAGE_RESULT MIGRATIONS_RESULT POSTGRES_RESULT \
    SHELL_RESULT; do
    : >"${GITHUB_OUTPUT}"
    run env "${var}=skipped" bash "${SCRIPT}"
    [ "${status}" -eq 1 ]
    [[ "${output}" == *"(skipped on a non-draft run)"* ]]
    assert_outputs failure false
  done
}

@test "names a skipped PostgreSQL lane on a non-draft run" {
  MIGRATIONS_RESULT=skipped POSTGRES_RESULT=skipped run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"test-postgres-migrations (skipped on a non-draft run)"* ]]
  [[ "${output}" == *"test-postgres-application (skipped on a non-draft run)"* ]]
  assert_outputs failure false
}

@test "a draft PR still fails on a failed job" {
  IS_DRAFT=true POSTGRES_RESULT=failure run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"test-postgres-application (failure)"* ]]
  assert_outputs failure false
}

@test "fails closed when IS_DRAFT is unset" {
  unset IS_DRAFT
  run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"IS_DRAFT must be 'true' or 'false'"* ]]
  assert_outputs failure false
}

@test "fails closed when IS_DRAFT is not a boolean" {
  local value
  for value in "" yes TRUE 1; do
    : >"${GITHUB_OUTPUT}"
    IS_DRAFT="${value}" run bash "${SCRIPT}"
    [ "${status}" -eq 1 ]
    [[ "${output}" == *"IS_DRAFT must be 'true' or 'false'"* ]]
    assert_outputs failure false
  done
}

@test "fails when any single upstream job fails" {
  local var
  for var in TEST_RESULT COVERAGE_RESULT MIGRATIONS_RESULT POSTGRES_RESULT \
    SHELL_RESULT; do
    : >"${GITHUB_OUTPUT}"
    run env "${var}=failure" bash "${SCRIPT}"
    [ "${status}" -eq 1 ]
    [[ "${output}" == *"::error::Upstream test jobs failed:"*"(failure)"* ]]
    assert_outputs failure false
  done
}

@test "fails when any single upstream job is cancelled" {
  local var
  for var in TEST_RESULT COVERAGE_RESULT MIGRATIONS_RESULT POSTGRES_RESULT \
    SHELL_RESULT; do
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

@test "names a failing PostgreSQL application lane" {
  POSTGRES_RESULT=failure run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"test-postgres-application (failure)"* ]]
  assert_outputs failure false
}

@test "fails when the PostgreSQL application result is unset" {
  unset POSTGRES_RESULT
  run bash "${SCRIPT}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"POSTGRES_RESULT is not set"* ]]
  assert_outputs failure false
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
  assert_outputs failure false
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
  [[ "${output}" == *"POSTGRES_RESULT"* ]]
  [[ "${output}" == *"IS_DRAFT"* ]]
}

@test "rejects unexpected arguments" {
  run bash "${SCRIPT}" --bogus
  [ "${status}" -eq 2 ]
}
