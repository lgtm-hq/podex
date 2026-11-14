#!/usr/bin/env bash
# =============================================================================
# lgtm-ci pin sync guard (#42)
# -----------------------------------------------------------------------------
# Fails when the lgtm-ci pins in .github/workflows drift apart. A caller can
# pin lgtm-ci twice: in the `uses:` reference (owned by Renovate's
# github-actions manager) and in a `tooling-ref:` input (owned by the org
# preset's custom regex manager). Two managers for one logical pin means the
# halves can drift, so this guard enforces:
#
#   1. Shape: every lgtm-ci `uses:` is pinned to a full 40-character SHA with a
#      `# vX.Y.Z` comment, and every `tooling-ref:` line is a quoted or
#      unquoted 40-character SHA with a `# vX.Y.Z` comment. Anything else is
#      reported, never skipped.
#   2. Ownership: a `tooling-ref:` must repeat the SHA and version comment of
#      the lgtm-ci `uses:` pin in the same job. A `tooling-ref:` in a job with
#      no lgtm-ci `uses:` is an error.
#   3. Recovery: the release-recovery reusables keep a required explicit
#      `tooling-ref` (it selects the tooling run against an older release), so
#      a recovery caller without one fails. Its value is held to rule 2 like
#      any other tooling-ref; there is no divergence allowance.
#   4. Uniformity: every lgtm-ci pin in the directory resolves to one SHA and
#      one version comment. There are no per-file exceptions.
#
# A caller with no `tooling-ref:` (the supported default since lgtm-ci
# v0.75.3) is checked only by rules 1 and 4.
#
# Usage:
#   check-lgtm-ci-pin-sync.sh [workflow-dir]   (default: .github/workflows)
#
# Exit codes: 0 consistent, 1 drift found, 2 usage error.
# =============================================================================
set -euo pipefail

# Reusables whose `tooling-ref` input is required and must stay explicit.
readonly RECOVERY_REUSABLES=(
	".github/workflows/reusable-release-recover.yml"
	".github/workflows/reusable-release-recover-plan.yml"
)

workflow_dir=".github/workflows"

while (($# > 0)); do
	case "$1" in
	--help | -h)
		sed -n '2,31p' "$0"
		exit 0
		;;
	-*)
		echo "Unknown argument: $1" >&2
		exit 2
		;;
	*)
		workflow_dir="$1"
		shift
		;;
	esac
done

if [[ -z "${workflow_dir}" || ! -d "${workflow_dir}" ]]; then
	echo "Workflow directory not found: ${workflow_dir}" >&2
	exit 2
fi

sha_re='^[0-9a-f]{40}$'
version_re='^v[0-9]+\.[0-9]+\.[0-9]+$'
# `uses: lgtm-hq/lgtm-ci/<path>@<ref>  # <comment>`, optionally a list item,
# optionally quoted. Ref and comment are validated separately so a malformed
# pin is reported rather than silently ignored.
uses_re="^[[:space:]]*(-[[:space:]]+)?uses:[[:space:]]*[\"']?lgtm-hq/lgtm-ci/([^@\"'[:space:]]+)@([^\"'[:space:]#]*)[\"']?[[:space:]]*(#[[:space:]]*([^[:space:]]*))?"
uses_any_re='^[[:space:]]*(-[[:space:]]+)?uses:.*lgtm-hq/lgtm-ci'
ref_re="^[[:space:]]*tooling-ref:[[:space:]]*([\"']?)([0-9a-f]{40})([\"']?)[[:space:]]*#[[:space:]]*(v[0-9]+\.[0-9]+\.[0-9]+)[[:space:]]*$"
ref_any_re='^[[:space:]]*tooling-ref:'
job_re="^  [\"']?([A-Za-z_][A-Za-z0-9_-]*)[\"']?:[[:space:]]*(#.*)?$"

violations=""
# One "<sha> <version>\t<file>:<line>" record per pin, for the uniformity check.
all_pins=""
tooling_refs_checked=0
uses_checked=0

add_violation() {
	violations="${violations}$1"$'\n'
}

is_recovery_reusable() {
	local path="$1" recovery
	for recovery in "${RECOVERY_REUSABLES[@]}"; do
		[[ "${path}" == "${recovery}" ]] && return 0
	done
	return 1
}

# Per-job state, reset by start_job and evaluated by finish_job. current_file
# is the workflow being scanned, used only in messages.
current_file=""
job_name=""
job_uses=""
job_refs=""
job_recovery_line=""

start_job() {
	job_name="$1"
	job_uses=""
	job_refs=""
	job_recovery_line=""
}

finish_job() {
	local record pin location
	[[ -z "${job_name}" ]] && return 0

	if [[ -n "${job_recovery_line}" && -z "${job_refs}" ]]; then
		add_violation "${current_file}:${job_recovery_line}: job '${job_name}' calls a release-recovery reusable, which requires an explicit tooling-ref equal to its uses: pin"
	fi

	while IFS= read -r record; do
		[[ -z "${record}" ]] && continue
		pin="${record%%$'\t'*}"
		location="${record#*$'\t'}"
		tooling_refs_checked=$((tooling_refs_checked + 1))
		if [[ -z "${job_uses}" ]]; then
			add_violation "${location}: job '${job_name}' has tooling-ref '${pin}' but no lgtm-ci uses: pin"
		elif ! cut -f1 <<<"${job_uses}" | grep -Fxq "${pin}"; then
			add_violation "${location}: job '${job_name}' tooling-ref '${pin}' does not match its uses: pin '$(head -n1 <<<"${job_uses}" | cut -f1)'"
		fi
	done <<<"${job_refs}"
}

check_file() {
	local file="$1" line line_no=0 in_jobs=false
	local path ref comment pin

	current_file="${file}"
	start_job ""
	while IFS= read -r line || [[ -n "${line}" ]]; do
		line_no=$((line_no + 1))

		# Whole-line comments never carry pins.
		[[ "${line}" =~ ^[[:space:]]*# ]] && continue

		if [[ "${line}" =~ ^[^[:space:]] ]]; then
			finish_job
			start_job ""
			if [[ "${line}" =~ ^jobs:[[:space:]]*(#.*)?$ ]]; then
				in_jobs=true
			else
				in_jobs=false
			fi
			continue
		fi

		if [[ "${in_jobs}" == true && "${line}" =~ ${job_re} ]]; then
			finish_job
			start_job "${BASH_REMATCH[1]}"
			continue
		fi

		if [[ "${line}" =~ ${uses_any_re} ]]; then
			if [[ "${line}" =~ ${uses_re} ]]; then
				path="${BASH_REMATCH[2]}"
				ref="${BASH_REMATCH[3]}"
				comment="${BASH_REMATCH[5]}"
				if [[ ! "${ref}" =~ ${sha_re} || ! "${comment}" =~ ${version_re} ]]; then
					add_violation "${file}:${line_no}: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment: ${line#"${line%%[![:space:]]*}"}"
					continue
				fi
				pin="${ref} ${comment}"
				uses_checked=$((uses_checked + 1))
				job_uses="${job_uses}${pin}"$'\t'"${file}:${line_no}"$'\n'
				all_pins="${all_pins}${pin}"$'\t'"${file}:${line_no}"$'\n'
				if is_recovery_reusable "${path}"; then
					job_recovery_line="${line_no}"
				fi
			else
				add_violation "${file}:${line_no}: unparsable lgtm-ci uses: line: ${line#"${line%%[![:space:]]*}"}"
			fi
			continue
		fi

		if [[ "${line}" =~ ${ref_any_re} ]]; then
			if [[ "${line}" =~ ${ref_re} && "${BASH_REMATCH[1]}" == "${BASH_REMATCH[3]}" ]]; then
				pin="${BASH_REMATCH[2]} ${BASH_REMATCH[4]}"
				job_refs="${job_refs}${pin}"$'\t'"${file}:${line_no}"$'\n'
				all_pins="${all_pins}${pin}"$'\t'"${file}:${line_no}"$'\n'
			else
				add_violation "${file}:${line_no}: unparsable tooling-ref line (expected a 40-character SHA and a '# vX.Y.Z' comment): ${line#"${line%%[![:space:]]*}"}"
			fi
		fi
	done <"${file}"
	finish_job
}

while IFS= read -r file; do
	check_file "${file}"
done < <(find "${workflow_dir}" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) | LC_ALL=C sort)

distinct_pins="$(cut -f1 <<<"${all_pins}" | grep -v '^$' | LC_ALL=C sort -u || true)"
distinct_count="$(grep -c . <<<"${distinct_pins}" || true)"
if [[ "${distinct_count}" -gt 1 ]]; then
	add_violation "lgtm-ci is pinned to ${distinct_count} different SHA/version pairs; every pin must agree:"
	while IFS= read -r pin; do
		[[ -z "${pin}" ]] && continue
		first="$(grep -F "${pin}"$'\t' <<<"${all_pins}" | head -n1 | cut -f2)"
		count="$(grep -cF "${pin}"$'\t' <<<"${all_pins}" || true)"
		add_violation "  ${pin} (${count} pin(s), first at ${first})"
	done <<<"${distinct_pins}"
fi

if [[ -n "${violations}" ]]; then
	echo "lgtm-ci pin drift detected in ${workflow_dir}:" >&2
	printf '%s' "${violations}" | sed 's/^/  /' >&2
	if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
		echo "::error title=lgtm-ci pin drift::Every lgtm-ci uses: and tooling-ref pin must name the same SHA and version comment; see the log for each location."
	fi
	exit 1
fi

echo "lgtm-ci pin sync guard passed: ${uses_checked} uses: pin(s) and ${tooling_refs_checked} tooling-ref pin(s) agree on ${distinct_pins:-no lgtm-ci pin}."
