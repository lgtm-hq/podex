#!/usr/bin/env bats
# Tests for .github/scripts/check-lgtm-ci-pin-sync.sh (#42)

SHA_A="808da537a4e346ac1ab3b9e1df10142909b96bc6"
SHA_B="aaa960d8bfa543e532830c53e5446372c8e80c7e"

setup() {
  PROJECT_ROOT="$(cd "${BATS_TEST_DIRNAME}/../../.." && pwd)"
  SCRIPT="${PROJECT_ROOT}/.github/scripts/check-lgtm-ci-pin-sync.sh"
  WF="${BATS_TEST_TMPDIR}/workflows"
  mkdir -p "${WF}"
  unset GITHUB_ACTIONS
}

# reusable_caller <file> <job> <reusable> <uses-ref> <uses-comment> [tooling-ref line]
reusable_caller() {
  local file="$1" job="$2" reusable="$3" ref="$4" comment="$5" tooling="${6:-}"
  {
    echo "---"
    echo "name: ${job}"
    echo '"on":'
    echo "  pull_request:"
    echo "permissions: {}"
    echo "jobs:"
    echo "  ${job}:"
    echo "    # yamllint disable-line rule:line-length"
    echo "    uses: lgtm-hq/lgtm-ci/.github/workflows/${reusable}@${ref} # ${comment}"
    echo "    permissions:"
    echo "      contents: read"
    echo "    with:"
    echo "      job-name: '${job}'"
    if [[ -n "${tooling}" ]]; then
      echo "      ${tooling}"
    fi
  } >>"${WF}/${file}"
}

@test "passes when every uses: and tooling-ref agree" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: '${SHA_A}' # v0.98.3"
  reusable_caller tests.yml tests reusable-test-python.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: \"${SHA_A}\" # v0.98.3"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"2 uses: pin(s) and 2 tooling-ref pin(s) agree on ${SHA_A} v0.98.3"* ]]
}

@test "passes when callers omit tooling-ref" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller tests.yml tests reusable-test-python.yml "${SHA_A}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"2 uses: pin(s) and 0 tooling-ref pin(s)"* ]]
}

@test "accepts an unquoted tooling-ref" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: ${SHA_A} # v0.98.3"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
}

@test "fails when one workflow's uses: SHA drifts" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller tests.yml tests reusable-test-python.yml "${SHA_B}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"pinned to 2 different SHA/version pairs"* ]]
  [[ "${output}" == *"${SHA_B} v0.98.3 (1 pin(s), first at ${WF}/tests.yml:9)"* ]]
}

@test "fails when a tooling-ref SHA drifts from its uses: pin" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: '${SHA_B}' # v0.98.3"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"quality.yml:14: job 'quality' tooling-ref '${SHA_B} v0.98.3' does not match its uses: pin '${SHA_A} v0.98.3'"* ]]
}

@test "fails when the version comment disagrees for the same SHA" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: '${SHA_A}' # v0.98.2"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"does not match its uses: pin"* ]]
  [[ "${output}" == *"${SHA_A} v0.98.2"* ]]
}

@test "fails when uses: comments disagree across workflows" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller tests.yml tests reusable-test-python.yml "${SHA_A}" v0.98.4
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"pinned to 2 different SHA/version pairs"* ]]
}

@test "passes a recovery caller whose tooling-ref repeats its uses: pin" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller release-recover.yml recover reusable-release-recover.yml \
    "${SHA_A}" v0.98.3 "tooling-ref: '${SHA_A}' # v0.98.3"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"2 uses: pin(s) and 1 tooling-ref pin(s)"* ]]
}

@test "fails a recovery caller whose tooling-ref diverges" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller release-recover.yml recover reusable-release-recover-plan.yml \
    "${SHA_A}" v0.98.3 "tooling-ref: '${SHA_B}' # v0.84.1"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"job 'recover' tooling-ref '${SHA_B} v0.84.1' does not match"* ]]
  [[ "${output}" == *"pinned to 2 different SHA/version pairs"* ]]
}

@test "fails a recovery caller without a tooling-ref" {
  reusable_caller release-recover.yml recover reusable-release-recover.yml \
    "${SHA_A}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"release-recover.yml:9: job 'recover' calls a release-recovery reusable, which requires an explicit tooling-ref"* ]]
}

@test "matches tooling-ref to the uses: pin of its own job" {
  cat >"${WF}/mixed.yml" <<EOF
---
name: mixed
jobs:
  lint:
    uses: lgtm-hq/lgtm-ci/.github/workflows/reusable-quality-lint.yml@${SHA_A} # v0.98.3
  local:
    runs-on: ubuntu-26.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: some/action@3d3c42e5aac5ba805825da76410c181273ba90b1 # v1.0.0
        with:
          tooling-ref: '${SHA_A}' # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"mixed.yml:12: job 'local' has tooling-ref '${SHA_A} v0.98.3' but no lgtm-ci uses: pin"* ]]
}

@test "counts lgtm-ci composite actions used as steps" {
  cat >"${WF}/steps.yml" <<EOF
---
name: steps
jobs:
  build:
    runs-on: ubuntu-26.04
    steps:
      - name: Checkout
        uses: lgtm-hq/lgtm-ci/.github/actions/secure-checkout@${SHA_B} # v0.84.1
EOF
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"${SHA_B} v0.84.1 (1 pin(s), first at ${WF}/steps.yml:8)"* ]]
}

@test "fails an lgtm-ci uses: pinned to a tag" {
  reusable_caller quality.yml quality reusable-quality-lint.yml v0.98.3 v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"quality.yml:9: lgtm-ci uses: must be pinned to a full commit SHA"* ]]
}

@test "fails an lgtm-ci uses: without a version comment" {
  cat >"${WF}/bare.yml" <<EOF
---
jobs:
  lint:
    uses: lgtm-hq/lgtm-ci/.github/workflows/reusable-quality-lint.yml@${SHA_A}
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"bare.yml:4: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment"* ]]
}

@test "reports an unparsable tooling-ref instead of skipping it" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    'tooling-ref: ${{ github.sha }}'
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"quality.yml:14: unparsable tooling-ref line"* ]]
}

@test "reports a tooling-ref with mismatched quotes" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: '${SHA_A}\" # v0.98.3"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"unparsable tooling-ref line"* ]]
}

@test "ignores pins inside whole-line comments" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  echo "    # uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_B} # v0.84.1" \
    >>"${WF}/quality.yml"
  echo "      # tooling-ref: '${SHA_B}' # v0.84.1" >>"${WF}/quality.yml"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
}

@test "checks .yaml files as well as .yml" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  reusable_caller other.yaml other reusable-test-python.yml "${SHA_B}" v0.84.1
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"other.yaml:9"* ]]
}

@test "emits a GitHub error annotation on drift in Actions" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3 \
    "tooling-ref: '${SHA_B}' # v0.84.1"
  GITHUB_ACTIONS=true run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"::error title=lgtm-ci pin drift::"* ]]
}

@test "exits 2 for a missing workflow directory" {
  run bash "${SCRIPT}" "${BATS_TEST_TMPDIR}/absent"
  [ "${status}" -eq 2 ]
  [[ "${output}" == *"Workflow directory not found"* ]]
}

@test "exits 2 for an unknown option" {
  run bash "${SCRIPT}" --bogus
  [ "${status}" -eq 2 ]
}

@test "the repository's own workflows pass" {
  cd "${PROJECT_ROOT}"
  run bash "${SCRIPT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"lgtm-ci pin sync guard passed"* ]]
}
