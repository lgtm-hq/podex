#!/usr/bin/env bash
# =============================================================================
# SBOM release-asset upload (#451, lgtm-ci#935)
# -----------------------------------------------------------------------------
# Attaches the SBOM files that the `sbom` job left behind as a workflow
# artifact to the GitHub Release for the current tag. Inline replacement for
# lgtm-ci's reusable-sbom-release-upload.yml, which never checks out the
# caller repo and does not set GH_REPO, so `gh release upload` cannot resolve
# the target repository (lgtm-ci#935). Switch back to the reusable once that
# fix ships.
#
# Required environment variables:
#   GH_TOKEN          - token with contents: write for gh release upload
#   GH_REPO           - owner/repo the release lives in (github.repository)
#   RELEASE_TAG       - release tag to attach assets to (github.ref_name)
#   SBOM_ARTIFACT_DIR - directory the sbom artifact was downloaded into
# =============================================================================
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GH_REPO:?GH_REPO is required}"
: "${RELEASE_TAG:?RELEASE_TAG is required}"
: "${SBOM_ARTIFACT_DIR:?SBOM_ARTIFACT_DIR is required}"

if [[ ! -d "${SBOM_ARTIFACT_DIR}" ]]; then
	echo "::error::SBOM artifact directory not found: ${SBOM_ARTIFACT_DIR}" >&2
	exit 1
fi

mapfile -t files < <(find "${SBOM_ARTIFACT_DIR}" -type f ! -name '.*' | sort)
if [[ ${#files[@]} -eq 0 ]]; then
	echo "::error::No SBOM files found in ${SBOM_ARTIFACT_DIR}" >&2
	exit 1
fi

gh release upload "${RELEASE_TAG}" "${files[@]}" --clobber
echo "Uploaded ${#files[@]} SBOM file(s) to ${GH_REPO} release ${RELEASE_TAG}"
