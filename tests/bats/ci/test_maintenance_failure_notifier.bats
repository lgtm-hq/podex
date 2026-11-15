#!/usr/bin/env bats
# Check all maintenance callers and replay their conditions with upstream results.

assert_notification() {
	local workflow="$1" ref="$2" expected="$3"
	shift 3
	local result condition
	export MOCK_FAILURE=false MOCK_REF="${ref}"
	for result in "$@"; do
		if [[ "${result}" == failure ]]; then
			export MOCK_FAILURE=true
		fi
	done
	condition=$(yq -r '.jobs."notify-failure".if' "${workflow}")
	# Translate the verified GitHub expression to yq's boolean expression syntax.
	condition="${condition//failure()/env(MOCK_FAILURE)}"
	condition="${condition//github.ref/strenv(MOCK_REF)}"
	condition="${condition//\&\&/and}"
	condition="${condition//\'/\"}"
	run yq -n "${condition}"
	[ "${status}" -eq 0 ]
	[ "${output}" = "${expected}" ]
}

@test "test_maintenance_failure_reaches_notifier" {
	local key workflow package failed_package
	local results=()
	# Follow the existing notifier pin so routine pin updates need no test edit.
	export EXPECTED_NOTIFIER
	EXPECTED_NOTIFIER=$(yq -r '.jobs."notify-failure".uses' \
		"${BATS_TEST_DIRNAME}/../../../.github/workflows/quality-ci.yml")
	for key in vuln-suppression-check scorecards ghcr-cleanup; do
		workflow="${BATS_TEST_DIRNAME}/../../../.github/workflows/${key}.yml"
		export WORKFLOW_KEY="${key}"
		run yq -e '
      (.permissions | length) == 0 and
      (."on".schedule | length) > 0 and
      (."on" | has("workflow_dispatch")) and
      (.jobs."notify-failure".needs | sort | join(",")) ==
        ([.jobs | keys | .[] | select(. != "notify-failure")] | sort | join(",")) and
      .jobs."notify-failure".if ==
        "failure() && github.ref == '\''refs/heads/main'\''" and
      .jobs."notify-failure".uses == strenv(EXPECTED_NOTIFIER) and
      (.jobs."notify-failure".permissions | length) == 3 and
      .jobs."notify-failure".permissions.actions == "read" and
      .jobs."notify-failure".permissions.contents == "read" and
      .jobs."notify-failure".permissions.issues == "write" and
      (.jobs."notify-failure".with | length) == 2 and
      .jobs."notify-failure".with."egress-policy" == "block" and
      .jobs."notify-failure".with."workflow-key" == strenv(WORKFLOW_KEY) and
      (.jobs."notify-failure" | has("continue-on-error") | not) and
      (.jobs."notify-failure" | has("strategy") | not)
    ' "${workflow}"
		[ "${status}" -eq 0 ]

		assert_notification "${workflow}" refs/heads/main true failure
		assert_notification "${workflow}" refs/heads/main false success
		assert_notification "${workflow}" refs/heads/main false skipped
		assert_notification "${workflow}" refs/heads/main false cancelled
		assert_notification "${workflow}" refs/heads/feature false failure
		assert_notification "${workflow}" refs/tags/v1.0.0 false failure
	done

	# A dependency on prune waits for every leg, without fail-fast cancellation.
	workflow="${BATS_TEST_DIRNAME}/../../../.github/workflows/ghcr-cleanup.yml"
	run yq -e '
    .jobs.prune.strategy."fail-fast" == false and
    (.jobs.prune.strategy.matrix.package | join(",")) ==
      "podex-backend,podex-frontend" and
    (.jobs."notify-failure".needs | join(",")) == "prune"
  ' "${workflow}"
	[ "${status}" -eq 0 ]
	for failed_package in $(yq -r '.jobs.prune.strategy.matrix.package[]' "${workflow}"); do
		results=()
		for package in $(yq -r '.jobs.prune.strategy.matrix.package[]' "${workflow}"); do
			if [[ "${package}" == "${failed_package}" ]]; then
				results+=(failure)
			else
				results+=(success)
			fi
		done
		assert_notification "${workflow}" refs/heads/main true "${results[@]}"
	done
	assert_notification "${workflow}" refs/heads/main true failure failure
	assert_notification "${workflow}" refs/heads/main false success success
}
