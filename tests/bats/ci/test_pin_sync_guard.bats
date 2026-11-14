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

# --- YAML forms: the guard parses workflows rather than matching columns ---

@test "fails two different SHAs when one uses: key is double-quoted" {
  cat >"${WF}/quoted.yml" <<EOF
---
jobs:
  a:
    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
  b:
    "uses": "lgtm-hq/lgtm-ci/.github/workflows/y.yml@${SHA_B}" # v0.84.1
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"pinned to 2 different SHA/version pairs"* ]]
  [[ "${output}" == *"${SHA_B} v0.84.1 (1 pin(s), first at ${WF}/quoted.yml:6)"* ]]
}

@test "fails a double-quoted lgtm-ci uses: pinned to a tag" {
  cat >"${WF}/tag.yml" <<EOF
---
jobs:
  a:
    "uses": "lgtm-hq/lgtm-ci/.github/workflows/x.yml@v0.98.3"
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"tag.yml:4: lgtm-ci uses: must be pinned to a full commit SHA"* ]]
}

@test "fails a recovery caller indented by four spaces without tooling-ref" {
  cat >"${WF}/recover.yml" <<EOF
---
jobs:
    recover:
        uses: lgtm-hq/lgtm-ci/.github/workflows/reusable-release-recover.yml@${SHA_A} # v0.98.3
        with:
            job-name: recover
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"recover.yml:4: job 'recover' calls a release-recovery reusable, which requires an explicit tooling-ref"* ]]
}

@test "passes single- and double-quoted keys and values that agree" {
  cat >"${WF}/quotes.yml" <<EOF
---
"jobs":
  'single':
    'uses': 'lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A}' # v0.98.3
    'with':
      'tooling-ref': '${SHA_A}' # v0.98.3
  "double":
    "uses": "lgtm-hq/lgtm-ci/.github/workflows/y.yml@${SHA_A}" # v0.98.3
    "with":
      "tooling-ref": "${SHA_A}" # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"2 uses: pin(s) and 2 tooling-ref pin(s) agree on ${SHA_A} v0.98.3"* ]]
}

@test "fails a quoted tooling-ref key that drifts from its uses: pin" {
  cat >"${WF}/quoted-ref.yml" <<EOF
---
jobs:
  a:
    'uses': "lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A}" # v0.98.3
    "with":
      'tooling-ref': "${SHA_B}" # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"quoted-ref.yml:6: job 'a' tooling-ref '${SHA_B} v0.98.3' does not match its uses: pin '${SHA_A} v0.98.3'"* ]]
}

@test "checks jobs at any indentation width" {
  cat >"${WF}/wide.yml" <<EOF
---
jobs:
      wide:
            uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
            with:
                     tooling-ref: '${SHA_A}' # v0.98.3
EOF
  cat >"${WF}/narrow.yml" <<EOF
---
jobs:
 narrow:
  uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
  with:
   tooling-ref: '${SHA_B}' # v0.84.1
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"narrow.yml:6: job 'narrow' tooling-ref '${SHA_B} v0.84.1' does not match"* ]]
  [[ "${output}" != *"wide.yml"*"does not match"* ]]
}

@test "verifies a uses: inside a multi-line flow mapping" {
  cat >"${WF}/flow.yml" <<EOF
---
jobs:
  a: {
    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_B}, # v0.84.1
  }
EOF
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"${SHA_B} v0.84.1 (1 pin(s), first at ${WF}/flow.yml:4)"* ]]
}

@test "rejects a single-line flow mapping whose comment is not on the value" {
  cat >"${WF}/flow.yml" <<EOF
---
jobs:
  a: {uses: "lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A}", with: {tooling-ref: '${SHA_A}'}} # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"flow.yml:3: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment on the same line"* ]]
  [[ "${output}" == *"flow.yml:3: unparsable tooling-ref line"* ]]
}

@test "rejects a uses: written as a multi-line block scalar" {
  cat >"${WF}/block.yml" <<EOF
---
jobs:
  a:
    uses: >-
      lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A}
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"block.yml:4: lgtm-ci uses: must be pinned"* ]]
  [[ "${output}" == *"block.yml:5: unverifiable lgtm-ci reference"* ]]
}

@test "decodes escapes in double-quoted values" {
  cat >"${WF}/escaped.yml" <<EOF
---
jobs:
  a:
    "u\\x73es": "lgtm-hq/lgtm\\x2Dci/.github/workflows/x.yml@${SHA_B}" # v0.84.1
EOF
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"${SHA_B} v0.84.1 (1 pin(s), first at ${WF}/escaped.yml:4)"* ]]
}

@test "matches the lgtm-ci owner and repository case-insensitively" {
  cat >"${WF}/upper.yml" <<EOF
---
jobs:
  a:
    uses: LGTM-HQ/LGTM-CI/.github/workflows/x.yml@${SHA_A} # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"upper.yml:4: lgtm-ci uses: must be pinned"* ]]
}

@test "resolves aliases and requires the version comment where they are used" {
  cat >"${WF}/alias.yml" <<EOF
---
jobs:
  a:
    uses: &pin lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
  b:
    uses: *pin
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"alias.yml:6: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment on the same line"* ]]
}

@test "holds an aliased recovery caller to the recovery rule" {
  cat >"${WF}/alias.yml" <<EOF
---
jobs:
  a:
    uses: &pin lgtm-hq/lgtm-ci/.github/workflows/reusable-release-recover.yml@${SHA_A} # v0.98.3
    with:
      tooling-ref: '${SHA_A}' # v0.98.3
  b:
    uses: *pin # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"alias.yml:8: job 'b' calls a release-recovery reusable"* ]]
  [[ "${output}" != *"job 'a' calls"* ]]
}

@test "rejects YAML merge keys" {
  cat >"${WF}/merge.yml" <<EOF
---
jobs:
  a:
    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
    with:
      <<: {tooling-ref: '${SHA_A}'}
      tooling-ref: '${SHA_A}' # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"merge.yml:6: YAML merge key (<<) is not supported"* ]]
}

@test "rejects a duplicate uses: key that shadows another pin" {
  cat >"${WF}/dup.yml" <<EOF
---
jobs:
  a:
    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_B} # v0.84.1
    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"dup.yml:4: unverifiable lgtm-ci reference"* ]]
}

@test "rejects a tooling-ref outside any job input" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  echo "env:" >>"${WF}/quality.yml"
  echo "  tooling-ref: '${SHA_B}' # v0.84.1" >>"${WF}/quality.yml"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"quality.yml:15: unparsable tooling-ref line (not a job or step input"* ]]
}

@test "ignores commented-out uses: lines at any indentation" {
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  {
    echo "#uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@v0.84.1"
    echo "        #   uses: 'lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_B}' # v0.84.1"
    echo "  # - \"uses\": lgtm-hq/lgtm-ci/.github/actions/a@main"
  } >>"${WF}/quality.yml"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"1 uses: pin(s) and 0 tooling-ref pin(s) agree on ${SHA_A} v0.98.3"* ]]
}

@test "parses workflows with Windows line endings" {
  printf -- '---\r\njobs:\r\n  a:\r\n    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@%s # v0.98.3\r\n    with:\r\n      tooling-ref: "%s" # v0.98.3\r\n    # uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@v1\r\n' \
    "${SHA_A}" "${SHA_A}" >"${WF}/crlf.yml"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"1 uses: pin(s) and 1 tooling-ref pin(s) agree on ${SHA_A} v0.98.3."* ]]
}

@test "detects drift in workflows with Windows line endings" {
  printf -- '---\r\njobs:\r\n  a:\r\n    uses: lgtm-hq/lgtm-ci/.github/workflows/x.yml@%s # v0.98.3\r\n    with:\r\n      tooling-ref: %s # v0.84.1\r\n' \
    "${SHA_A}" "${SHA_B}" >"${WF}/crlf.yml"
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"crlf.yml:6: job 'a' tooling-ref '${SHA_B} v0.84.1' does not match its uses: pin '${SHA_A} v0.98.3'"* ]]
}

@test "reports a workflow that is not valid YAML" {
  cat >"${WF}/broken.yml" <<EOF
---
jobs:
  a:
    uses: "lgtm-hq/lgtm-ci/.github/workflows/x.yml@${SHA_A} # v0.98.3
EOF
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"broken.yml: cannot parse as YAML"* ]]
  [[ "${output}" == *"broken.yml:4: unverifiable lgtm-ci reference"* ]]
}

@test "exits 2 when mikefarah yq v4 is not available" {
  mkdir -p "${BATS_TEST_TMPDIR}/bin"
  printf '#!/bin/sh\necho "yq 3.4.1"\n' >"${BATS_TEST_TMPDIR}/bin/yq"
  chmod +x "${BATS_TEST_TMPDIR}/bin/yq"
  reusable_caller quality.yml quality reusable-quality-lint.yml "${SHA_A}" v0.98.3
  PATH="${BATS_TEST_TMPDIR}/bin:${PATH}" run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 2 ]
  [[ "${output}" == *"yq (mikefarah/yq v4) is required"* ]]
}

@test "the repository's own workflows pass without tooling-ref inputs" {
  cp "${PROJECT_ROOT}"/.github/workflows/*.yml "${WF}/"
  local file
  for file in "${WF}"/*.yml; do
    grep -v '^[[:space:]]*tooling-ref:' "${file}" >"${file}.tmp"
    mv "${file}.tmp" "${file}"
  done
  run bash "${SCRIPT}" "${WF}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"0 tooling-ref pin(s)"* ]]
}
