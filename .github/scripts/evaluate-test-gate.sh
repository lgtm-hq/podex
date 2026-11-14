#!/usr/bin/env bash
# =============================================================================
# Combined test gate (#550)
# -----------------------------------------------------------------------------
# Evaluates the upstream test job results behind the required
# `test-suite / 🧪 Test Suite` context. Adapted from lgtm-hq/py-lintro
# scripts/ci/evaluate-test-gate.sh.
#
# - "success" passes.
# - "skipped" passes only on a draft pull request (IS_DRAFT=true), the one
#   case where test jobs skip. On a ready pull request, the merge queue or a
#   push to main a skipped job fails: nothing ran, so nothing was proven.
# - "failure" and "cancelled" fail.
# - Any other value, including an empty one, fails closed; so does an
#   IS_DRAFT other than "true" or "false".
#
# When GITHUB_OUTPUT is set, writes `result` (success|failure) and `passed`
# (true|false) for the downstream reusable-required-check caller.
# =============================================================================
set -euo pipefail

usage() {
	cat <<'EOF'
Evaluate upstream test job results for the required test gate.

Passes when every upstream job is "success", or "skipped" on a draft pull
request. Fails when any job is "failure" or "cancelled", is "skipped" on a
non-draft run, or reports an unexpected value.

Usage:
  IS_DRAFT=false TEST_RESULT=success COVERAGE_RESULT=success \
    MIGRATIONS_RESULT=success POSTGRES_RESULT=success SHELL_RESULT=success \
    .github/scripts/evaluate-test-gate.sh

Required environment variables:
  IS_DRAFT           "true" on a draft pull request, otherwise "false"
  TEST_RESULT        needs.test.result (Python compatibility matrix)
  COVERAGE_RESULT    needs.test-coverage.result
  MIGRATIONS_RESULT  needs.test-postgres-migrations.result
  POSTGRES_RESULT    needs.test-postgres-application.result
  SHELL_RESULT       needs.test-shell.result (BATS tests)

Optional environment variables:
  GITHUB_OUTPUT      file that receives result=... and passed=...
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
	usage
	exit 0
fi

if (($# > 0)); then
	echo "Unexpected argument: $1" >&2
	usage >&2
	exit 2
fi

write_outputs() {
	local result="$1"
	local passed="$2"
	if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
		{
			echo "result=${result}"
			echo "passed=${passed}"
		} >>"${GITHUB_OUTPUT}"
	fi
}

case "${IS_DRAFT-}" in
true | false) ;;
*)
	echo "::error::IS_DRAFT must be 'true' or 'false', got: '${IS_DRAFT-<unset>}'" >&2
	write_outputs failure false
	exit 1
	;;
esac

jobs=(
	"test:TEST_RESULT"
	"test-coverage:COVERAGE_RESULT"
	"test-postgres-migrations:MIGRATIONS_RESULT"
	"test-postgres-application:POSTGRES_RESULT"
	"test-shell:SHELL_RESULT"
)

failed=()
for entry in "${jobs[@]}"; do
	name="${entry%%:*}"
	var="${entry##*:}"
	if [[ -z "${!var+x}" ]]; then
		echo "::error::${var} is not set" >&2
		write_outputs failure false
		exit 1
	fi
	result="${!var}"
	printf '%-27s %s\n' "${name}:" "${result:-<empty>}"
	case "${result}" in
	success) ;;
	skipped)
		if [[ "${IS_DRAFT}" != "true" ]]; then
			failed+=("${name} (skipped on a non-draft run)")
		fi
		;;
	failure | cancelled) failed+=("${name} (${result})") ;;
	*) failed+=("${name} (unexpected result: '${result}')") ;;
	esac
done

if ((${#failed[@]} > 0)); then
	printf '::error::Upstream test jobs failed: %s\n' "${failed[*]}"
	write_outputs failure false
	exit 1
fi

if [[ "${IS_DRAFT}" == "true" ]]; then
	echo "All upstream test jobs passed or were skipped (draft pull request)"
else
	echo "All upstream test jobs passed"
fi
write_outputs success true
