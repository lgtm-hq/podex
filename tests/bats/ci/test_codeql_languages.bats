#!/usr/bin/env bats
# Exercise the caller contract and optionally replay actual CI extraction logs.

@test "test_codeql_covers_all_executable_languages" {
	local root="${BATS_TEST_DIRNAME}/../../.."
	local workflow="${root}/.github/workflows/codeql.yml"
	local config="${root}/.github/codeql/codeql-config.yml"

	run yq -e '
    .jobs.codeql.with.languages == "python,javascript-typescript,actions" and
    .jobs.codeql.with."build-mode" == "none" and
    .jobs.codeql.with."job-name" == "🔬 CodeQL Analysis" and
    (.jobs.codeql.with | has("category") | not) and
    (.jobs.codeql.with | has("language-build-modes") | not) and
    .jobs.codeql.with."config-file" == ".github/codeql/codeql-config.yml" and
    .jobs.codeql.permissions.contents == "read" and
    .jobs.codeql.permissions.actions == "read" and
    .jobs.codeql.permissions."security-events" == "write" and
    .jobs.codeql.with."egress-policy" == "block" and
    (.jobs."notify-failure".needs | length) == 1 and
    .jobs."notify-failure".needs[0] == "codeql" and
    .jobs."notify-failure".if == "failure() && github.ref == '\''refs/heads/main'\''" and
    (."on".push.branches | length) == 1 and
    ."on".push.branches[0] == "main" and
    (."on".pull_request.branches | length) == 1 and
    ."on".pull_request.branches[0] == "main" and
    (."on".pull_request | has("paths") | not) and
    (."on".pull_request | has("paths-ignore") | not) and
    (."on" | has("merge_group")) and
    (."on".schedule | length) > 0
  ' "${workflow}"
	[ "${status}" -eq 0 ]

	# There is no positive path allowlist. The existing omissions are confined
	# to backend tests/migrations, leaving frontend and .github sources in scope,
	# and the default query suite is neither replaced nor filtered.
	run yq -e '
    (has("paths") | not) and
    (has("queries") | not) and
    (has("query-filters") | not) and
    (has("disable-default-queries") | not) and
    (."paths-ignore" | all_c(. | test("^backend/")))
  ' "${config}"
	[ "${status}" -eq 0 ]

	# Supply the three completed analysis-job logs to verify the contract
	# against CodeQL output, not a synthetic source-selection fixture:
	# PODEX_CODEQL_LOG_DIR=/path/to/logs bats --filter \
	#   test_codeql_covers_all_executable_languages tests/bats/ci/test_codeql_languages.bats
	# Name the downloaded job logs python.log, javascript-typescript.log, actions.log.
	if [[ -n "${PODEX_CODEQL_LOG_DIR:-}" ]]; then
		local language log
		for language in python javascript-typescript actions; do
			log="${PODEX_CODEQL_LOG_DIR}/${language}.log"
			[ -s "${log}" ]
			run grep -F "category: /language:${language}" "${log}"
			[ "${status}" -eq 0 ]
			run grep -F 'Successfully uploaded results' "${log}"
			[ "${status}" -eq 0 ]
		done
		run grep -E 'Done extracting .*/frontend/.*\.(ts|tsx|js|jsx|mjs) ' \
			"${PODEX_CODEQL_LOG_DIR}/javascript-typescript.log"
		[ "${status}" -eq 0 ]
		run grep -E 'Done extracting .*/\.github/workflows/.*\.ya?ml ' \
			"${PODEX_CODEQL_LOG_DIR}/actions.log"
		[ "${status}" -eq 0 ]
	fi
}
