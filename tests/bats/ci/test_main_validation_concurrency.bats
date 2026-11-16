#!/usr/bin/env bats
# Concurrency contract for every workflow (#44). Each workflow file belongs to
# exactly one class below. Concurrency groups and cancel-in-progress values are
# evaluated with GitHub's expression semantics (helpers/github_expression.py)
# for concrete events, and the resulting runs are replayed through GitHub's
# queueing rule: per group at most one run in progress and one pending; a new
# run replaces the pending one, and also cancels the running one when
# cancel-in-progress is true.

WORKFLOWS="${BATS_TEST_DIRNAME}/../../../.github/workflows"
EXPRESSION="${BATS_TEST_DIRNAME}/helpers/github_expression.py"

# Validate main: a push to main must leave a completed run for every commit.
MAIN_VALIDATION=(all-checks-gate codeql docker-ci frontend-ci pin-sync-guard
	quality-ci sbom security-audit test-ci)
# Push to main but mutate the repository: stay serialized repository-wide.
RELEASE_MUTATORS=(release-auto-tag release-version-pr)
# Scheduled maintenance: unchanged scheduling and serialization.
MAINTENANCE=(ghcr-cleanup scorecards vuln-suppression-check)
# Pull request (and merge queue) checks only.
PR_ONLY=(ai-review dependency-review pr-auto-assign pr-labeler
	semantic-pr-title validate-action-pinning)
# Triggered by other workflows' completed runs.
RUN_FOLLOWERS=(auto-rerun-on-infra-failure)
# Callers without a workflow-level block: ai-review's pinned reusable sets a
# per-PR group; the others run once per event and need none. Every other
# workflow must keep its block.
NO_CALLER_CONCURRENCY=(ai-review pr-auto-assign pr-labeler
	auto-rerun-on-infra-failure)

SHA_A=1111111111111111111111111111111111111111
SHA_B=2222222222222222222222222222222222222222
SHA_C=3333333333333333333333333333333333333333
MAIN=refs/heads/main

# github_context EVENT REF SHA [PR_NUMBER] -> expression context JSON. Only
# these properties are modelled; the helper fails on any other one it has to
# evaluate. `event.pull_request` is null on events without a pull request, as
# on GitHub.
github_context() {
	jq -cn --arg event "$1" --arg ref "$2" --arg sha "$3" --arg pr "${4:-}" \
		--arg workflow "${WORKFLOW_NAME}" '
    {github: {
      event_name: $event, ref: $ref, sha: $sha,
      repository: "lgtm-hq/podex", workflow: $workflow,
      event: {pull_request:
        (if $pr == "" then null else {number: ($pr | tonumber)} end)}
    }}'
}

# group_of WORKFLOW EVENT REF SHA [PR_NUMBER] -> rendered concurrency group.
group_of() {
	local file="${WORKFLOWS}/$1.yml" template context
	shift
	WORKFLOW_NAME="$(yq -r '.name' "${file}")" || return 1
	template="$(yq -r '.concurrency.group' "${file}")" || return 1
	[[ "${template}" != null ]] || return 1
	context="$(github_context "$@")" || return 1
	python3 -I "${EXPRESSION}" render "${template}" "${context}"
}

# cancel_of WORKFLOW EVENT REF SHA [PR_NUMBER] -> true or false.
cancel_of() {
	local file="${WORKFLOWS}/$1.yml" template context
	shift
	WORKFLOW_NAME="$(yq -r '.name' "${file}")" || return 1
	template="$(yq -r '.concurrency."cancel-in-progress"' "${file}")" || return 1
	[[ "${template}" != null ]] || return 1
	context="$(github_context "$@")" || return 1
	python3 -I "${EXPRESSION}" truthy "${template}" "${context}"
}

# arrival ID WORKFLOW EVENT REF SHA [PR_NUMBER] -> "ID<TAB>group<TAB>cancel".
arrival() {
	local id="$1" group cancel
	shift
	group="$(group_of "$@")" || return 1
	cancel="$(cancel_of "$@")" || return 1
	printf '%s\t%s\t%s\n' "${id}" "${group}" "${cancel}"
}

# replay ARRIVAL... -> IDs of runs cancelled when every run is queued before
# any finishes, in cancellation order (empty when none is).
replay() {
	local -a ids=() groups=() states=()
	local entry id group cancel i has_running cancelled=""
	for entry in "$@"; do
		IFS=$'\t' read -r id group cancel <<<"${entry}"
		has_running=false
		for ((i = 0; i < ${#ids[@]}; i++)); do
			[[ "${groups[i]}" == "${group}" ]] || continue
			if [[ "${states[i]}" == pending ]] ||
				[[ "${states[i]}" == running && "${cancel}" == true ]]; then
				states[i]=cancelled
				cancelled="${cancelled} ${ids[i]}"
			elif [[ "${states[i]}" == running ]]; then
				has_running=true
			fi
		done
		ids+=("${id}")
		groups+=("${group}")
		if [[ "${has_running}" == true ]]; then
			states+=(pending)
		else
			states+=(running)
		fi
	done
	echo "${cancelled# }"
}

# `on` as one object keyed by event. GitHub also accepts a list of event names
# or a single name; any other form is an error.
TRIGGERS_JQ='."on"
  | if type == "object" then .
    elif type == "array" then (map({(tostring): null}) | add // {})
    elif type == "string" then {(.): null}
    else error("on must be a mapping, a list or an event name") end'

# Whether a push to main runs the workflow, from `on` as `triggers` prints it:
# "true", "false", "pattern: REASON" when a person has to decide, or
# "invalid: REASON" for a filter that has to be fixed instead. Branch filters
# are read only as lists of literal branch names. Anything GitHub reads as a
# pattern (`*`, `**`, `?`, `+`, `[...]`, a leading `!`, a `\` escape) is not
# interpreted. Invalid: both branches and branches-ignore, which GitHub
# rejects; a filter that is not a list; an empty list; an empty or non-string
# entry; branches with only negated patterns, which GitHub rejects. Tag filters
# need no matching: without a branch filter, their presence alone means a
# branch push does not run the workflow.
PUSH_TO_MAIN_JQ='
  def invalid($why): error({invalid: $why});
  def names($key): .[$key] as $names | ($names | tojson) as $shown
    | if ($names | type) != "array" then invalid("push.\($key) is \($shown), not a list")
      elif ($names | length) == 0 then invalid("push.\($key) is an empty list")
      elif ($names | any(type != "string" or length == 0)) then
        invalid("push.\($key) has an empty or non-string entry: \($shown)")
      elif $key == "branches" and ($names | all(startswith("!"))) then
        invalid("push.branches is \($shown), only negated patterns")
      elif ($names | any(test("^!|[*?+\\[\\]\\\\]"))) then
        error({pattern: "push.\($key) is \($shown), not a list of literal branch names"})
      else $names end;
  try (
    if has("push") | not then false
    else (.push // {}) as $p
      | if ($p | type) != "object" then true
        elif ($p | has("branches")) and ($p | has("branches-ignore")) then
          invalid("push sets both branches and branches-ignore")
        elif ($p | has("branches")) then $p | names("branches") | any(. == "main")
        elif ($p | has("branches-ignore")) then
          $p | names("branches-ignore") | all(. != "main")
        else ($p | has("tags") or has("tags-ignore")) | not end
    end | tostring
  ) catch (if type == "object" and has("pattern") then "pattern: \(.pattern)"
    elif type == "object" and has("invalid") then "invalid: \(.invalid)"
    else "invalid: \(.)" end)'

# The push filters a hand classification is pinned to.
PUSH_REF_FILTERS_JQ='.push | with_entries(select(.key
  | IN("branches", "branches-ignore", "tags", "tags-ignore")))'

# Workflows whose push filters PUSH_TO_MAIN_JQ leaves to a person (patterns
# only, never invalid filters), classified by hand against GitHub's filter
# pattern rules: "WORKFLOW VERDICT FILTER", where VERDICT is true when a push
# to main runs the workflow and FILTER is its push branch and tag filters
# exactly as the failure message prints them. An entry applies only to an
# identical filter, so it stops applying as soon as the filter changes. A
# false entry cannot make the suite pass: only validation and release
# workflows may have a push trigger, and they must run on main.
PUSH_FILTERS_BY_HAND=()

triggers() {
	yq -o=json '.' "${WORKFLOWS}/$1.yml" | jq -ce "${TRIGGERS_JQ}"
}

# trigger_verdict WORKFLOW EVENT -> true or false: whether `on` names EVENT.
# Fails when `on` cannot be read. Capture the output, as for
# main_push_verdict; never test the function itself in a condition, where a
# failure would read as "false".
trigger_verdict() {
	local events
	events="$(triggers "$1")" || return 1
	jq -r --arg event "$2" 'has($event)' <<<"${events}"
}

require_trigger() {
	local verdict
	verdict="$(trigger_verdict "$1" "$2")" || return 1
	if [[ "${verdict}" != true ]]; then
		echo "$1: needs a $2 trigger" >&2
		return 1
	fi
}

lacks_trigger() {
	local verdict
	verdict="$(trigger_verdict "$1" "$2")" || return 1
	if [[ "${verdict}" != false ]]; then
		echo "$1: unexpected $2 trigger" >&2
		return 1
	fi
}

# main_push_verdict WORKFLOW -> true or false: whether a push to main runs it.
# Fails, saying what to do, when its push filters need a person. Callers
# capture the output, so the failure is never read as "false".
main_push_verdict() {
	local events verdict filter entry name hand_verdict hand_filter
	events="$(triggers "$1")" || return 1
	verdict="$(jq -r "${PUSH_TO_MAIN_JQ}" <<<"${events}")" || return 1
	case "${verdict}" in
	true | false)
		echo "${verdict}"
		return 0
		;;
	"invalid: "*)
		echo "$1: ${verdict#invalid: }. Fix the push filter; it cannot be" \
			"classified by hand." >&2
		return 1
		;;
	"pattern: "*) ;;
	*)
		echo "$1: unexpected push verdict: ${verdict}" >&2
		return 1
		;;
	esac
	filter="$(jq -cS "${PUSH_REF_FILTERS_JQ}" <<<"${events}")" || return 1
	for entry in "${PUSH_FILTERS_BY_HAND[@]}"; do
		read -r name hand_verdict hand_filter <<<"${entry}"
		[[ "${name}" == "$1" && "${hand_filter}" == "${filter}" ]] || continue
		if [[ "${hand_verdict}" != true && "${hand_verdict}" != false ]]; then
			echo "PUSH_FILTERS_BY_HAND: the verdict for $1 must be true or false" >&2
			return 1
		fi
		echo "${hand_verdict}"
		return 0
	done
	echo "$1: ${verdict#pattern: }. This test matches literal branch names" \
		"only. Decide from GitHub's filter pattern rules whether a push to main" \
		"runs $1, then classify it by hand in PUSH_FILTERS_BY_HAND:" \
		"$1 <true|false> ${filter}" >&2
	return 1
}

no_caller_concurrency() {
	[[ " ${NO_CALLER_CONCURRENCY[*]} " == *" $1 "* ]]
}

# require_concurrency WORKFLOW: fails when the block is missing or cannot be
# read.
require_concurrency() {
	local group
	group="$(yq -r '.concurrency.group' "${WORKFLOWS}/$1.yml")" || return 1
	if [[ -z "${group}" || "${group}" == null ]]; then
		echo "$1: needs its concurrency block (group and cancel-in-progress)" >&2
		return 1
	fi
}

@test "test_expression_helper_follows_github_semantics" {
	local context
	WORKFLOW_NAME=CI
	context="$(github_context push "${MAIN}" "${SHA_A}")"
	check() {
		run python3 -I "${EXPRESSION}" "$1" "$2" "${context}"
		[ "${status}" -eq 0 ]
		[ "${output}" = "$3" ]
	}
	# A reported error: status 3 and the `expression error:` prefix. Any other
	# failure is a crash of the helper, which must never pass for an error.
	reported_error() {
		[ "$1" -eq 3 ] && [[ "$2" == "expression error: "* ]]
	}
	fails() {
		run python3 -I "${EXPRESSION}" "$1" "$2" "${context}"
		reported_error "${status}" "${output}"
		[[ "${output}" == *"${3:-}"* ]]
	}
	check render "\${{ 'a' == 'A' }}" true
	check render "x\${{ null }}y\${{ github.event.pull_request.number }}" xy
	check render "\${{ false && 'a' || 'b' }}" b
	check render "\${{ 'a' && 'b' }}" b
	check render "\${{ '' || 0 || 'c' }}" c
	check render "\${{ 7 == '7' }}" true
	check render "\${{ !(github.ref != 'refs/heads/MAIN') }}" true
	check render "\${{ format('-{0}{{1}}', github.sha) }}" "-${SHA_A}{1}"
	check render "\${{ format('{{0}}-{0}', 'a') }}" "{0}-a"
	check render "\${{ 'it''s' }}" "it's"
	check render "\${{ startsWith('refs/tags/v1', 'refs/tags/v') }}" true
	check render "\${{ github.event.pull_request.number || github.ref }}" \
		"${MAIN}"
	check truthy "\${{ github.event_name == 'pull_request' }}" false
	check truthy " \${{ github.sha }} " true
	check truthy false false

	# && and || short-circuit: the operand that decides nothing is not
	# evaluated, so its runtime error never happens.
	check render "\${{ true || format('{1}', 'x') }}" true
	check render "\${{ false && format('{1}', 'x') }}" false
	check render "\${{ 0 && format('{1}', 'x') }}" 0
	check render "\${{ 'a' || github.missing.key }}" a
	check render "\${{ github.event_name == 'pull_request' && github.run_id || 'x' }}" x

	# Falsy: false, 0, '', null. Everything else, '0' and 'false' included, is
	# truthy.
	check truthy "\${{ 0 }}" false
	check truthy "\${{ '' }}" false
	check truthy "\${{ null }}" false
	check truthy "\${{ '0' }}" true
	check truthy "\${{ 'false' }}" true
	check truthy "\${{ github.event }}" true

	# Mixed types compare as numbers: null and false are 0, true is 1, a string
	# is its JSON number (empty or blank is 0) or NaN, and NaN equals nothing.
	check render "\${{ null == 0 }}" true
	check render "\${{ false == '' }}" true
	check render "\${{ true == '1' }}" true
	check render "\${{ ' 7 ' == 7 }}" true
	check render "\${{ '1e1' == 10 }}" true
	check render "\${{ 0.5 == 0.50 }}" true
	check render "\${{ '1_0' == 10 }}" false
	check render "\${{ 'abc' == 0 }}" false
	check render "\${{ 'abc' != 'ABC' }}" false
	check render "\${{ github.event == 0 }}" false
	# Ordinal ignore-case: one character never matches two.
	check render "\${{ 'ß' == 'SS' }}" false
	# Context and property names ignore case; a property of null or of a
	# non-object is null.
	check render "\${{ GitHub.Event_Name }}" push
	check render "\${{ github.Ref != 'refs/heads/main' }}" false
	check render "x\${{ github.sha.length }}" x
	check render "\${{ 999999999999999 }}" 999999999999999

	# Outside the supported subset is an error, never a guess.
	fails render "\${{ github.ref > 1 }}"
	fails render "\${{ hashFiles('x') }}"
	fails truthy "a\${{ true }}"
	# A property the context does not define fails, naming it, wherever it is
	# evaluated.
	fails render "\${{ github.missing.key }}" "github.missing is not in the test's context"
	fails render "\${{ github.event.after }}" "github.event.after is not"
	fails render "\${{ github.event.head_commit.id }}" "github.event.head_commit is not"
	fails render "\${{ github.event_name == 'push' && github.run_number || '' }}" "github.run_number"
	fails truthy "\${{ github.ref_name == 'main' }}" "github.ref_name"
	fails render "\${{ '' || github.run_id }}" "github.run_id"
	# Objects and arrays are not strings: GitHub renders `Object` or `Array`.
	fails render "\${{ github.event }}" "an object used as a string"
	fails render "\${{ format('-{0}', github) }}" "an object used as a string"
	fails render "\${{ format(github.event, 'x') }}" "an object used as a string"
	fails render "\${{ startsWith(github.event, 'x') }}" "an object used as a string"
	fails render "\${{ contains(github, 'sha') }}" "an object used as a string"
	# Format strings: only `{{`, `}}` and `{N}` with ASCII digits and no
	# leading zero, and at least one replacement argument.
	fails render "\${{ format('{-{0}', github.sha) }}" "invalid format string"
	fails render "\${{ format('}', 'a') }}" "invalid format string"
	fails render "\${{ format('{0', 'a') }}" "invalid format string"
	fails render "\${{ format('{00}', 'a') }}" "invalid format string"
	fails render "\${{ format('{0:x}', 'a') }}" "invalid format string"
	fails render "\${{ format('{١}', 'a') }}" "invalid format string"
	fails render "\${{ format('x') }}" "wrong number of arguments for format"
	# Literals: ASCII digits only, no leading zeros.
	fails render "\${{ ١ }}" "unsupported syntax"
	fails render "\${{ 007 }}" "leading zeros"
	# Numbers render as GitHub does only as integers below 1e15.
	fails render "\${{ 1000000000000000 }}" "rendering the number"
	fails render "\${{ 0.5 }}" "rendering the number"
	# Non-ASCII case mapping is not modelled where it decides a result.
	fails render "\${{ 'é' == 'É' }}" "non-ASCII case"
	fails render "\${{ startsWith('Éa', 'é') }}" "non-ASCII case"
	# The runner reads these strings as numbers; GitHub documents only JSON
	# numbers.
	fails render "\${{ '0x10' == 16 }}"
	fails render "\${{ '+1' == 1 }}"
	# An evaluated operand still fails.
	fails render "\${{ true && format('{1}', 'x') }}"
	# The whole expression is parsed first: syntax, unknown functions, wrong
	# argument counts and unknown contexts fail even where not evaluated.
	fails render "\${{ true || (false }}"
	fails render "\${{ true || hashFiles('x') }}"
	fails render "\${{ true || startsWith('a') }}"
	fails render "\${{ false && secrets.token }}"

	# A crash is not a reported error: the helper dies on a context that is
	# not JSON, with exit status 1 and a traceback.
	run python3 -I "${EXPRESSION}" render "\${{ true }}" '{'
	[ "${status}" -eq 1 ]
	if reported_error "${status}" "${output}"; then return 1; fi
	# Each half of reported_error rejects a crash on its own.
	if reported_error 1 "expression error: x"; then return 1; fi
	if reported_error 3 "Traceback (most recent call last):"; then return 1; fi
}

@test "test_push_to_main_detection_reads_literal_branch_names_only" {
	verdict() {
		run jq -r "${PUSH_TO_MAIN_JQ}" <<<"$1"
		[ "${status}" -eq 0 ]
		[[ "${output}" == "$2"* ]]
	}
	verdict '{"push":null}' true
	verdict '{"push":{"branches":["main"]}}' true
	verdict '{"push":{"branches":["develop","main"],"paths":["src/**"]}}' true
	verdict '{"push":{"branches":["develop"]}}' false
	verdict '{"push":{"branches-ignore":["main"]}}' false
	verdict '{"push":{"branches-ignore":["develop"]}}' true
	verdict '{"push":{"paths":["src/**"]}}' true
	# Tag filters alone keep branch pushes out; with a branch filter, the
	# branch filter decides. Tag patterns are never matched.
	verdict '{"push":{"tags":["v*.*.*"]}}' false
	verdict '{"push":{"tags-ignore":["v*"]}}' false
	verdict '{"push":{"branches":["main"],"tags":["v*.*.*"]}}' true
	verdict '{"push":{"branches":["develop"],"tags":["v*.*.*"]}}' false
	verdict '{"pull_request":null,"workflow_dispatch":null}' false

	# Patterns are left to a person.
	local pattern
	for pattern in '**' '*' 'release/**' 'main*' 'ma?in' 'mai+n' '[m]ain' \
		'ma\\in'; do
		verdict "{\"push\":{\"branches\":[\"${pattern}\"]}}" pattern
		verdict "{\"push\":{\"branches-ignore\":[\"${pattern}\"]}}" pattern
	done
	verdict '{"push":{"branches":["main","!main"]}}' pattern
	verdict '{"push":{"branches-ignore":["!main"]}}' pattern
	# Filters GitHub rejects, and forms this test does not accept, have to be
	# fixed.
	verdict '{"push":{"branches":["!main"]}}' invalid
	verdict '{"push":{"branches":"main"}}' invalid
	verdict '{"push":{"branches":[]}}' invalid
	verdict '{"push":{"branches":[""]}}' invalid
	verdict '{"push":{"branches-ignore":[]}}' invalid
	verdict '{"push":{"branches-ignore":[1]}}' invalid
	verdict '{"push":{"branches":["main"],"branches-ignore":["x"]}}' invalid

	# A pattern fails until it is classified by hand with that exact filter,
	# and again when its filter changes.
	WORKFLOWS="${BATS_TEST_TMPDIR}"
	local filter='{"branches-ignore":["**"]}'
	printf '%s\n' 'on:' '  push:' "    branches-ignore: ['**']" \
		>"${WORKFLOWS}/scratch.yml"
	run main_push_verdict scratch
	[ "${status}" -eq 1 ]
	[[ "${output}" == *"classify it by hand in PUSH_FILTERS_BY_HAND: scratch <true|false> ${filter}" ]]
	PUSH_FILTERS_BY_HAND=("scratch false ${filter}")
	run main_push_verdict scratch
	[ "${status}" -eq 0 ]
	[ "${output}" = false ]
	# Only an identical filter matches: not a missing or truncated filter, not
	# the filter with something appended, not another workflow's entry.
	local entry
	for entry in 'scratch false' "scratch false ${filter:0:$((${#filter} - 1))}" \
		"scratch false ${filter:0:5}" "scratch false ${filter}x" \
		"other false ${filter}"; do
		PUSH_FILTERS_BY_HAND=("${entry}")
		run main_push_verdict scratch
		[ "${status}" -eq 1 ]
		[[ "${output}" == *"classify it by hand"* ]]
	done
	printf '%s\n' 'on:' '  push:' "    branches: ['release/**']" \
		>"${WORKFLOWS}/scratch.yml"
	PUSH_FILTERS_BY_HAND=("scratch false ${filter}")
	run main_push_verdict scratch
	[ "${status}" -eq 1 ]
	PUSH_FILTERS_BY_HAND=('scratch maybe {"branches":["release/**"]}')
	run main_push_verdict scratch
	[ "${status}" -eq 1 ]

	# An invalid filter fails without offering the hand list, and an entry
	# for it does not apply.
	local branches
	for branches in "branches: [main]
    branches-ignore: [x]" "branches: []" "branches: ['']" "branches: ['!main']" \
		"branches: main"; do
		printf '%s\n' 'on:' '  push:' "    ${branches}" >"${WORKFLOWS}/scratch.yml"
		filter="$(yq -o=json '.' "${WORKFLOWS}/scratch.yml" |
			jq -cS "${TRIGGERS_JQ} | ${PUSH_REF_FILTERS_JQ}")"
		PUSH_FILTERS_BY_HAND=("scratch true ${filter}")
		run main_push_verdict scratch
		[ "${status}" -eq 1 ]
		[[ "${output}" == *"Fix the push filter; it cannot be classified by hand."* ]]
		[[ "${output}" != *PUSH_FILTERS_BY_HAND* ]]
	done
}

@test "test_every_workflow_concurrency_block_is_accounted_for" {
	local file name class_members count verdict files=0 pushes=0 classified
	class_members=" ${MAIN_VALIDATION[*]} ${RELEASE_MUTATORS[*]} ${MAINTENANCE[*]} ${PR_ONLY[*]} ${RUN_FOLLOWERS[*]} "
	classified=$((${#MAIN_VALIDATION[@]} + ${#RELEASE_MUTATORS[@]} + ${#MAINTENANCE[@]} + ${#PR_ONLY[@]} + ${#RUN_FOLLOWERS[@]}))
	for file in "${WORKFLOWS}"/*.yml "${WORKFLOWS}"/*.yaml; do
		[[ -e "${file}" ]] || continue
		files=$((files + 1))
		name="$(basename "${file%.*}")"
		count="$(tr ' ' '\n' <<<"${class_members}" | grep -cFx "${name}" || true)"
		if [[ "${count}" -ne 1 ]]; then
			echo "${name}: classified ${count} times; add it to exactly one class" >&2
			return 1
		fi
	done
	# Every classified name is a workflow file, so the loops below see them all.
	[ "${files}" -eq "${classified}" ]
	for name in ${class_members}; do
		[ -f "${WORKFLOWS}/${name}.yml" ]
		# Every `on` must be a form GitHub accepts and this test understands.
		triggers "${name}" >/dev/null
		if ! no_caller_concurrency "${name}"; then
			require_concurrency "${name}"
		fi
	done

	# Every workflow that runs on pushes to main is a validation or release one.
	for file in "${WORKFLOWS}"/*.yml; do
		name="$(basename "${file%.yml}")"
		verdict="$(main_push_verdict "${name}")"
		pushes=$((pushes + 1))
		if [[ "${verdict}" == true ]] &&
			[[ " ${MAIN_VALIDATION[*]} ${RELEASE_MUTATORS[*]} " != *" ${name} "* ]]; then
			echo "${name}: runs on pushes to main but is not a validation or release workflow" >&2
			return 1
		fi
	done
	[ "${pushes}" -eq "${classified}" ]
	for name in "${MAIN_VALIDATION[@]}" "${RELEASE_MUTATORS[@]}"; do
		verdict="$(main_push_verdict "${name}")"
		if [[ "${verdict}" != true ]]; then
			echo "${name}: a validation or release workflow that does not run on pushes to main" >&2
			return 1
		fi
	done
	for name in "${MAINTENANCE[@]}"; do
		require_trigger "${name}" schedule
		lacks_trigger "${name}" push
		lacks_trigger "${name}" pull_request
	done
	for name in "${PR_ONLY[@]}"; do
		lacks_trigger "${name}" push
		lacks_trigger "${name}" schedule
	done
	for name in "${RUN_FOLLOWERS[@]}"; do
		run yq -r '."on" | keys | join(",")' "${WORKFLOWS}/${name}.yml"
		[ "${output}" = workflow_run ]
	done
}

@test "test_every_concurrency_expression_evaluates_for_every_trigger" {
	# Each group and cancel-in-progress value is evaluated for every event
	# that starts the workflow, so an operand that only one event reaches is
	# evaluated too. An event without a model here fails the test.
	local name events event verdict tags runs=0 workflows=0 considered=0
	for name in "${MAIN_VALIDATION[@]}" "${RELEASE_MUTATORS[@]}" \
		"${MAINTENANCE[@]}" "${PR_ONLY[@]}" "${RUN_FOLLOWERS[@]}"; do
		considered=$((considered + 1))
		no_caller_concurrency "${name}" && continue
		require_concurrency "${name}"
		workflows=$((workflows + 1))
		events="$(triggers "${name}")"
		events="$(jq -r 'keys[]' <<<"${events}")"
		[ -n "${events}" ]
		for event in ${events}; do
			case "${event}" in
			push)
				verdict="$(main_push_verdict "${name}")"
				if [[ "${verdict}" == true ]]; then
					arrival p "${name}" push "${MAIN}" "${SHA_A}" >/dev/null
					runs=$((runs + 1))
				fi
				tags="$(yq -r '."on".push.tags // [] | length' "${WORKFLOWS}/${name}.yml")"
				if [[ "${tags}" -gt 0 ]]; then
					arrival t "${name}" push refs/tags/v1.2.3 "${SHA_A}" >/dev/null
					runs=$((runs + 1))
				fi
				;;
			pull_request)
				arrival r "${name}" pull_request refs/pull/7/merge "${SHA_A}" 7 >/dev/null
				runs=$((runs + 1))
				;;
			merge_group)
				arrival q "${name}" merge_group \
					"refs/heads/gh-readonly-queue/main/pr-7-${SHA_A}" "${SHA_A}" >/dev/null
				runs=$((runs + 1))
				;;
			workflow_dispatch)
				arrival d "${name}" workflow_dispatch "${MAIN}" "${SHA_A}" >/dev/null
				arrival o "${name}" workflow_dispatch refs/heads/other "${SHA_A}" >/dev/null
				runs=$((runs + 2))
				;;
			schedule)
				arrival s "${name}" schedule "${MAIN}" "${SHA_A}" >/dev/null
				runs=$((runs + 1))
				;;
			*)
				echo "${name}: no ${event} event is modelled here; add one" >&2
				return 1
				;;
			esac
		done
	done
	[ "${considered}" -eq $((${#MAIN_VALIDATION[@]} + ${#RELEASE_MUTATORS[@]} + ${#MAINTENANCE[@]} + ${#PR_ONLY[@]} + ${#RUN_FOLLOWERS[@]})) ]
	[ "${workflows}" -gt 0 ]
	[ "${runs}" -ge "${workflows}" ]
}

@test "test_main_validation_groups_differ_by_sha" {
	local name a b c
	for name in "${MAIN_VALIDATION[@]}"; do
		a="$(arrival "${name}-a" "${name}" push "${MAIN}" "${SHA_A}")"
		b="$(arrival "${name}-b" "${name}" push "${MAIN}" "${SHA_B}")"
		c="$(arrival "${name}-c" "${name}" push "${MAIN}" "${SHA_C}")"
		# Distinct groups, never cancel-in-progress on main.
		[ "$(cut -f2 <<<"${a}")" != "$(cut -f2 <<<"${b}")" ]
		[ "$(cut -f2 <<<"${b}")" != "$(cut -f2 <<<"${c}")" ]
		[ "$(cut -f2 <<<"${a}")" != "$(cut -f2 <<<"${c}")" ]
		[ "$(cut -f3 <<<"${a}")" = false ]
		# Three rapid pushes: none is replaced or cancelled.
		run replay "${a}" "${b}" "${c}"
		[ "${status}" -eq 0 ]
		if [ -n "${output}" ]; then
			echo "${name}: main runs cancelled: ${output}" >&2
			return 1
		fi
	done
}

@test "test_main_validation_jobs_have_no_concurrency_of_their_own" {
	# A job-level group would queue (or cancel) jobs across main commits again,
	# whatever the workflow-level group says. The pinned reusables' own blocks
	# are not visible here; check them when the lgtm-ci pin changes.
	local name jobs
	for name in "${MAIN_VALIDATION[@]}"; do
		jobs="$(yq -r '.jobs | to_entries | map(select(.value | has("concurrency")) | .key) | join(",")' \
			"${WORKFLOWS}/${name}.yml")"
		if [ -n "${jobs}" ]; then
			echo "${name}: job-level concurrency in ${jobs}" >&2
			return 1
		fi
	done
}

@test "test_main_validation_pushes_are_not_replaced_by_other_main_runs" {
	local name dispatch schedule entries=() examined=0 dispatched=0 scheduled=0
	for name in "${MAIN_VALIDATION[@]}"; do
		entries=("$(arrival "${name}-push-a" "${name}" push "${MAIN}" "${SHA_A}")")
		dispatch="$(trigger_verdict "${name}" workflow_dispatch)"
		schedule="$(trigger_verdict "${name}" schedule)"
		if [[ "${dispatch}" == true ]]; then
			dispatched=$((dispatched + 1))
			entries+=("$(arrival "${name}-dispatch-1" "${name}" workflow_dispatch "${MAIN}" "${SHA_A}")")
			entries+=("$(arrival "${name}-dispatch-2" "${name}" workflow_dispatch "${MAIN}" "${SHA_B}")")
			# Manual runs on main keep one group per ref and never cancel.
			[ "$(cut -f2 <<<"${entries[1]}")" = "$(cut -f2 <<<"${entries[2]}")" ]
			[ "$(cut -f3 <<<"${entries[1]}")" = false ]
		fi
		if [[ "${schedule}" == true ]]; then
			scheduled=$((scheduled + 1))
			entries+=("$(arrival "${name}-schedule" "${name}" schedule "${MAIN}" "${SHA_B}")")
			[ "$(cut -f3 <<<"${entries[${#entries[@]} - 1]}")" = false ]
		fi
		entries+=("$(arrival "${name}-push-b" "${name}" push "${MAIN}" "${SHA_B}")")
		entries+=("$(arrival "${name}-push-c" "${name}" push "${MAIN}" "${SHA_C}")")
		run replay "${entries[@]}"
		[ "${status}" -eq 0 ]
		# A third manual run may replace the pending second; no push run is
		# touched.
		if [[ " ${output} " == *"-push-"* ]]; then
			echo "${name}: main push runs cancelled: ${output}" >&2
			return 1
		fi
		examined=$((examined + 1))
	done
	[ "${examined}" -eq "${#MAIN_VALIDATION[@]}" ]
	[ "${dispatched}" -gt 0 ]
	[ "${scheduled}" -gt 0 ]
}

@test "test_pr_supersession_cancels_and_separate_prs_do_not" {
	local name ref=refs/pull/7/merge other_ref=refs/pull/8/merge
	local a b c other main_push verdict on_pr considered=0 examined=0
	for name in "${MAIN_VALIDATION[@]}" "${PR_ONLY[@]}"; do
		on_pr="$(trigger_verdict "${name}" pull_request)"
		considered=$((considered + 1))
		[[ "${on_pr}" == true ]] || continue
		no_caller_concurrency "${name}" && continue
		require_concurrency "${name}"
		a="$(arrival "${name}-pr7-a" "${name}" pull_request "${ref}" "${SHA_A}" 7)"
		b="$(arrival "${name}-pr7-b" "${name}" pull_request "${ref}" "${SHA_B}" 7)"
		c="$(arrival "${name}-pr7-c" "${name}" pull_request "${ref}" "${SHA_C}" 7)"
		other="$(arrival "${name}-pr8" "${name}" pull_request "${other_ref}" "${SHA_B}" 8)"
		# Repeated events on one PR share a group and cancel what they supersede.
		[ "$(cut -f2 <<<"${a}")" = "$(cut -f2 <<<"${c}")" ]
		run replay "${a}" "${b}" "${c}"
		[ "${output}" = "${name}-pr7-a ${name}-pr7-b" ]
		# Separate PRs never touch each other.
		run replay "${a}" "${other}"
		[ -z "${output}" ]
		verdict="$(main_push_verdict "${name}")"
		if [[ "${verdict}" == true ]]; then
			main_push="$(arrival "${name}-main" "${name}" push "${MAIN}" "${SHA_A}")"
			run replay "${main_push}" "${a}" "${b}" "${other}"
			[ "${output}" = "${name}-pr7-a" ]
		fi
		examined=$((examined + 1))
	done
	[ "${considered}" -eq $((${#MAIN_VALIDATION[@]} + ${#PR_ONLY[@]})) ]
	[ "${examined}" -gt 0 ]
}

@test "test_merge_group_runs_keep_their_own_groups" {
	local name q7 q8 main_push pr verdict queue=refs/heads/gh-readonly-queue/main
	local queued considered=0 examined=0
	for name in "${MAIN_VALIDATION[@]}" "${PR_ONLY[@]}"; do
		queued="$(trigger_verdict "${name}" merge_group)"
		considered=$((considered + 1))
		[[ "${queued}" == true ]] || continue
		no_caller_concurrency "${name}" && continue
		require_concurrency "${name}"
		q7="$(arrival q7 "${name}" merge_group "${queue}/pr-7-${SHA_A}" "${SHA_A}")"
		q8="$(arrival q8 "${name}" merge_group "${queue}/pr-8-${SHA_B}" "${SHA_B}")"
		pr="$(arrival pr7 "${name}" pull_request refs/pull/7/merge "${SHA_C}" 7)"
		[ "$(cut -f2 <<<"${q7}")" != "$(cut -f2 <<<"${q8}")" ]
		verdict="$(main_push_verdict "${name}")"
		if [[ "${verdict}" == true ]]; then
			main_push="$(arrival main "${name}" push "${MAIN}" "${SHA_A}")"
			run replay "${q7}" "${q8}" "${pr}" "${main_push}"
		else
			run replay "${q7}" "${q8}" "${pr}"
		fi
		[ "${status}" -eq 0 ]
		[ -z "${output}" ]
		examined=$((examined + 1))
	done
	[ "${considered}" -eq $((${#MAIN_VALIDATION[@]} + ${#PR_ONLY[@]})) ]
	[ "${examined}" -gt 0 ]
}

@test "test_release_tag_groups_stay_serialized_per_tag" {
	local name t1 t2 other main_push
	for name in docker-ci sbom; do
		yq -e '(."on".push.tags // []) | any_c(. == "v*.*.*")' \
			"${WORKFLOWS}/${name}.yml" >/dev/null
		t1="$(arrival t1 "${name}" push refs/tags/v1.2.3 "${SHA_A}")"
		t2="$(arrival t2 "${name}" push refs/tags/v1.2.3 "${SHA_B}")"
		other="$(arrival t3 "${name}" push refs/tags/v1.2.4 "${SHA_C}")"
		main_push="$(arrival main "${name}" push "${MAIN}" "${SHA_A}")"
		# One group per tag, no commit suffix, never cancelled.
		[ "$(cut -f2 <<<"${t1}")" = "$(cut -f2 <<<"${t2}")" ]
		[ "$(cut -f2 <<<"${t1}")" = "${name}-refs/tags/v1.2.3" ]
		[ "$(cut -f3 <<<"${t1}")" = false ]
		[ "$(cut -f2 <<<"${t1}")" != "$(cut -f2 <<<"${other}")" ]
		run replay "${t1}" "${t2}" "${other}" "${main_push}"
		[ "${status}" -eq 0 ]
		[ -z "${output}" ]
	done
}

@test "test_release_mutation_groups_stay_serialized" {
	local name a b dispatch
	for name in "${RELEASE_MUTATORS[@]}"; do
		a="$(arrival a "${name}" push "${MAIN}" "${SHA_A}")"
		b="$(arrival b "${name}" push "${MAIN}" "${SHA_B}")"
		dispatch="$(arrival d "${name}" workflow_dispatch "${MAIN}" "${SHA_C}")"
		# One repository-wide group for every event, never cancel-in-progress.
		[ "$(cut -f2 <<<"${a}")" = "${name}-lgtm-hq/podex" ]
		[ "$(cut -f2 <<<"${b}")" = "$(cut -f2 <<<"${a}")" ]
		[ "$(cut -f2 <<<"${dispatch}")" = "$(cut -f2 <<<"${a}")" ]
		[ "$(cut -f3 <<<"${a}")" = false ]
		[ "$(cut -f3 <<<"${dispatch}")" = false ]
		# The reusable's own job group stays distinct from the caller's.
		run yq -r '.jobs | to_entries | .[0].value.uses' "${WORKFLOWS}/${name}.yml"
		[ "${output%@*}" = "lgtm-hq/lgtm-ci/.github/workflows/reusable-${name}.yml" ]
	done
}

@test "test_maintenance_scheduling_is_unchanged" {
	local schedule dispatch name
	schedule="$(arrival s ghcr-cleanup schedule "${MAIN}" "${SHA_A}")"
	dispatch="$(arrival d ghcr-cleanup workflow_dispatch refs/heads/other "${SHA_B}")"
	[ "$(cut -f2 <<<"${schedule}")" = "Maintenance - GHCR Registry Cleanup" ]
	[ "$(cut -f2 <<<"${dispatch}")" = "$(cut -f2 <<<"${schedule}")" ]
	[ "$(cut -f3 <<<"${schedule}")" = false ]

	schedule="$(arrival s vuln-suppression-check schedule "${MAIN}" "${SHA_A}")"
	dispatch="$(arrival d vuln-suppression-check workflow_dispatch refs/heads/other "${SHA_B}")"
	[ "$(cut -f2 <<<"${schedule}")" = "vuln-suppression-check-lgtm-hq/podex" ]
	[ "$(cut -f2 <<<"${dispatch}")" = "$(cut -f2 <<<"${schedule}")" ]
	[ "$(cut -f3 <<<"${schedule}")" = false ]

	# Scorecards cancels an in-progress run on the same ref (documented in
	# .github/scripts/README.md).
	schedule="$(arrival s scorecards schedule "${MAIN}" "${SHA_A}")"
	[ "$(cut -f2 <<<"${schedule}")" = "scorecards-${MAIN}" ]
	[ "$(cut -f3 <<<"${schedule}")" = true ]

	for name in "${NO_CALLER_CONCURRENCY[@]}"; do
		run yq -r '.concurrency' "${WORKFLOWS}/${name}.yml"
		[ "${output}" = null ]
	done
}

@test "test_all_checks_gate_judges_its_own_commit" {
	local file="${WORKFLOWS}/all-checks-gate.yml"
	# No head-sha input: the pinned reusable then gates github.sha on a push,
	# the commit whose per-commit group this run holds.
	run yq -e '
    (.jobs | keys | join(",")) == "gate" and
    (.jobs.gate.uses | test("^lgtm-hq/lgtm-ci/.github/workflows/reusable-all-checks-gate.yml@[0-9a-f]{40}$")) and
    (.jobs.gate.with | has("head-sha") | not)
  ' "${file}"
	[ "${status}" -eq 0 ]
}
