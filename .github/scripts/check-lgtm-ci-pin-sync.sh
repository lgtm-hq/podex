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
#      `# vX.Y.Z` comment, and every `tooling-ref:` is a 40-character SHA with
#      a `# vX.Y.Z` comment. Anything else is reported, never skipped.
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
# Workflows are read with yq (mikefarah/yq v4, preinstalled on GitHub-hosted
# Ubuntu runners), so quoting, indentation, flow style, escapes, aliases and
# CRLF line endings resolve exactly as a YAML parser sees them. The version
# comment is not YAML data: it is taken from the comment yq attaches to the
# pinned value, so it must follow that value on the same line. The guard
# fails closed: a merge key (`<<`), a file yq cannot parse, or any
# uncommented source line naming lgtm-hq/lgtm-ci or tooling-ref that does not
# hold a value verified above is reported as unverifiable.
#
# Usage:
#   check-lgtm-ci-pin-sync.sh [workflow-dir]   (default: .github/workflows)
#
# Exit codes: 0 consistent, 1 drift found, 2 usage or environment error.
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
		sed -n '2,39p' "$0"
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

if ! command -v yq >/dev/null 2>&1 || ! yq --version 2>&1 | grep -q 'mikefarah/yq.* v4\.'; then
	echo "yq (mikefarah/yq v4) is required to parse workflows; it is preinstalled on GitHub-hosted Ubuntu runners." >&2
	exit 2
fi

readonly LGTM_CI_RE='[Ll][Gg][Tt][Mm]-[Hh][Qq]/[Ll][Gg][Tt][Mm]-[Cc][Ii]'
readonly TOOLING_REF_RE='[Tt][Oo][Oo][Ll][Ii][Nn][Gg][-_][Rr][Ee][Ff]'
sha_re='^[0-9a-f]{40}$'
version_re='^v[0-9]+\.[0-9]+\.[0-9]+$'
uses_re='^lgtm-hq/lgtm-ci(/[^@[:space:]]+)?@([^@[:space:]]*)$'

# Record extraction lives in a yq program file beside this script (see its
# header for the record format).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RECORDS_PROGRAM="${script_dir}/check-lgtm-ci-pin-sync.yq"
if [[ ! -f "${RECORDS_PROGRAM}" ]]; then
	echo "Guard program not found: ${RECORDS_PROGRAM}" >&2
	exit 2
fi
readonly YQ_MERGE_LINES='.. | select(key == "<<") | line'

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
job_key=""
job_uses=""
job_refs=""
job_has_job_ref=false
job_recovery_line=""

start_job() {
	job_key="$1"
	job_uses=""
	job_refs=""
	job_has_job_ref=false
	job_recovery_line=""
}

finish_job() {
	local record pin location job_name
	[[ -z "${job_key}" ]] && return 0
	job_name="${job_key#*:}"

	if [[ -n "${job_recovery_line}" && "${job_has_job_ref}" != true ]]; then
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

# Lines (one per entry, as ":<n>:") holding a value this file's records cover.
verified_lines=""

check_uses() {
	local value="$1" tag="$2" comment="$3" line_no="$4" path ref pin
	# Only lgtm-ci references are pinned here; other actions are not ours.
	[[ "${value}" =~ ${LGTM_CI_RE} ]] || return 0
	verified_lines="${verified_lines}:${line_no}:"
	if [[ "${tag}" != "!!str" || ! "${value}" =~ ${uses_re} ]]; then
		add_violation "${current_file}:${line_no}: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment: ${value}"
		return 0
	fi
	path="${BASH_REMATCH[1]#/}"
	ref="${BASH_REMATCH[2]}"
	if [[ ! "${ref}" =~ ${sha_re} || ! "${comment}" =~ ${version_re} ]]; then
		add_violation "${current_file}:${line_no}: lgtm-ci uses: must be pinned to a full commit SHA with a '# vX.Y.Z' comment on the same line: ${value}${comment:+ # ${comment}}"
		return 0
	fi
	pin="${ref} ${comment}"
	uses_checked=$((uses_checked + 1))
	job_uses="${job_uses}${pin}"$'\t'"${current_file}:${line_no}"$'\n'
	all_pins="${all_pins}${pin}"$'\t'"${current_file}:${line_no}"$'\n'
	if is_recovery_reusable "${path}"; then
		job_recovery_line="${line_no}"
	fi
}

check_ref() {
	local kind="$1" value="$2" tag="$3" comment="$4" line_no="$5" pin
	verified_lines="${verified_lines}:${line_no}:"
	if [[ "${tag}" != "!!str" || ! "${value}" =~ ${sha_re} || ! "${comment}" =~ ${version_re} ]]; then
		add_violation "${current_file}:${line_no}: unparsable tooling-ref line (expected a 40-character SHA and a '# vX.Y.Z' comment on the same line): ${value}${comment:+ # ${comment}}"
		return 0
	fi
	pin="${value} ${comment}"
	job_refs="${job_refs}${pin}"$'\t'"${current_file}:${line_no}"$'\n'
	all_pins="${all_pins}${pin}"$'\t'"${current_file}:${line_no}"$'\n'
	[[ "${kind}" == "ref" ]] && job_has_job_ref=true
	return 0
}

# Fail closed: every uncommented source line that names lgtm-hq/lgtm-ci or
# tooling-ref must hold a value the parsed checks above verified. Catches
# forms the parse does not attribute to a job pin (values split across lines,
# duplicate keys, pins outside jobs, unparsable files).
check_unverified_lines() {
	local file="$1" line line_no=0 text
	while IFS= read -r line || [[ -n "${line}" ]]; do
		line_no=$((line_no + 1))
		line="${line%$'\r'}"
		[[ "${line}" =~ ^[[:space:]]*(#|$) ]] && continue
		[[ "${verified_lines}" == *":${line_no}:"* ]] && continue
		text="${line#"${line%%[![:space:]]*}"}"
		if [[ "${line}" =~ ${TOOLING_REF_RE} ]]; then
			add_violation "${file}:${line_no}: unparsable tooling-ref line (not a job or step input the guard can verify): ${text}"
		elif [[ "${line}" =~ ${LGTM_CI_RE} ]]; then
			add_violation "${file}:${line_no}: unverifiable lgtm-ci reference (write it as a single-line uses: value followed by '# vX.Y.Z'): ${text}"
		fi
	done <"${file}"
}

check_file() {
	local file="$1" records record merges kind job value tag comment line_no yq_error

	current_file="${file}"
	verified_lines=""
	start_job ""

	if ! merges="$(yq "${YQ_MERGE_LINES}" "${file}" 2>&1)"; then
		yq_error="$(head -n1 <<<"${merges}")"
		add_violation "${file}: cannot parse as YAML: ${yq_error}"
		check_unverified_lines "${file}"
		return 0
	fi
	if [[ -n "${merges}" ]]; then
		while IFS= read -r line_no; do
			add_violation "${file}:${line_no}: YAML merge key (<<) is not supported by the pin guard; write the keys out"
		done <<<"${merges}"
		check_unverified_lines "${file}"
		return 0
	fi

	if ! records="$(yq --from-file "${RECORDS_PROGRAM}" "${file}" 2>&1)"; then
		yq_error="$(head -n1 <<<"${records}")"
		add_violation "${file}: cannot read workflow jobs: ${yq_error}"
		check_unverified_lines "${file}"
		return 0
	fi

	while IFS= read -r record; do
		[[ -z "${record}" ]] && continue
		# Split by hand: IFS=$'\t' would merge the empty comment field.
		kind="${record%%$'\t'*}" && record="${record#*$'\t'}"
		job="${record%%$'\t'*}" && record="${record#*$'\t'}"
		value="${record%%$'\t'*}" && record="${record#*$'\t'}"
		tag="${record%%$'\t'*}" && record="${record#*$'\t'}"
		comment="${record%%$'\t'*}" && line_no="${record#*$'\t'}"
		# yq drops '# ' but keeps a bare '#'; normalise to the comment text.
		comment="${comment#\#}"
		comment="${comment#"${comment%%[![:space:]]*}"}"
		comment="${comment%"${comment##*[![:space:]]}"}"
		if [[ "${job}" != "${job_key}" ]]; then
			finish_job
			start_job "${job}"
		fi
		case "${kind}" in
		uses) check_uses "${value}" "${tag}" "${comment}" "${line_no}" ;;
		ref | stepref) check_ref "${kind}" "${value}" "${tag}" "${comment}" "${line_no}" ;;
		*) add_violation "${file}: unexpected guard record: ${kind}" ;;
		esac
	done <<<"${records}"
	finish_job
	check_unverified_lines "${file}"
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
