#!/usr/bin/env bash
# =============================================================================
# Upgrade Debian packages without exact-version pins, then assert minimums.
# -----------------------------------------------------------------------------
# Exact `pkg=version` pins break the next trixie-security revision: apt fails
# with "Version … was not found", or a newer base image would request a
# downgrade. `--only-upgrade` installs whatever is current; dpkg then fails
# the build if the installed version is older than the known-fixed floor.
#
# Usage: upgrade-min-debs.sh pkg=minver [pkg=minver ...]
# =============================================================================
set -euo pipefail

usage() {
	echo "Usage: $0 pkg=minver [pkg=minver ...]" >&2
	exit 2
}

if [[ $# -lt 1 ]]; then
	usage
fi

packages=()
min_versions=()

for spec in "$@"; do
	if [[ "${spec}" != *=* || "${spec}" == =* || "${spec}" == *= ]]; then
		echo "invalid spec: ${spec} (expected pkg=minver)" >&2
		exit 2
	fi
	packages+=("${spec%%=*}")
	min_versions+=("${spec#*=}")
done

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends --only-upgrade "${packages[@]}"

apt_lists_dir="${APT_LISTS_DIR:-/var/lib/apt/lists}"
if [[ -d "${apt_lists_dir}" ]]; then
	rm -rf "${apt_lists_dir:?}/"*
fi

for i in "${!packages[@]}"; do
	pkg="${packages[$i]}"
	min="${min_versions[$i]}"
	installed="$(dpkg-query -W -f='${Version}' "${pkg}")"
	if ! dpkg --compare-versions "${installed}" ge "${min}"; then
		echo "${pkg} ${installed} is older than required ${min}" >&2
		exit 1
	fi
done
