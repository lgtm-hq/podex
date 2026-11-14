#!/usr/bin/env bats
# Keep the tag-only SBOM producer/upload handoff and token scopes aligned.

@test "test_sbom_artifact_contract_matches_upload" {
	local workflow="${BATS_TEST_DIRNAME}/../../../.github/workflows/sbom.yml"
	local producer consumer
	producer=$(yq -r '.jobs.sbom.with."artifact-name"' "${workflow}")
	consumer=$(yq -r '.jobs."sbom-release-upload".with."artifact-name"' "${workflow}")
	[ "${producer}" = "podex-sbom" ]
	[ "${consumer}" = "${producer}" ]
	run yq -r '.jobs."sbom-release-upload".with."release-tag"' "${workflow}"
	[ "${status}" -eq 0 ]
	[ "${output}" = "\${{ github.ref_name }}" ]

	# Quoting the on key avoids YAML 1.1 parsers treating it as a boolean.
	run yq -e '
    (."on".push.branches | length) == 1 and
    ."on".push.branches[0] == "main" and
    (."on".push.tags | length) == 1 and
    ."on".push.tags[0] == "v*.*.*" and
    (."on" | has("workflow_dispatch")) and
    .jobs.sbom.with."sbom-format" == "cyclonedx-json" and
    .jobs.sbom.permissions.contents == "read" and
    .jobs."sbom-release-upload".needs == "sbom" and
    .jobs."sbom-release-upload".if == "startsWith(github.ref, '\''refs/tags/v'\'')" and
    .jobs."sbom-release-upload".permissions.contents == "write" and
    .jobs."sbom-release-upload".with."egress-policy" == "block" and
    (.jobs."sbom-release-upload".uses | split("@")[0]) ==
      "lgtm-hq/lgtm-ci/.github/workflows/reusable-sbom-release-upload.yml" and
    ([.jobs[] | select(.permissions.contents == "write")] | length) == 1
  ' "${workflow}"
	[ "${status}" -eq 0 ]
}
