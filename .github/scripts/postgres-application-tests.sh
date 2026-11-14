#!/usr/bin/env bash
# =============================================================================
# PostgreSQL application tests (#724)
# -----------------------------------------------------------------------------
# Runs every test marked `postgres` against the CI PostgreSQL/pgvector service.
# Each test gets its own database migrated with `alembic upgrade head` (see
# backend/tests/postgres/conftest.py). `--require-postgres` turns a missing
# PostgreSQL into a test failure instead of a skip, and pytest exits non-zero
# when no test is selected, so this lane cannot pass without running them.
# PODEX_DATABASE__URL must point at a disposable server (the fixture creates
# and force-drops whole databases there) as a superuser (pgvector needs one).
# =============================================================================
set -euo pipefail

# Tests set PODEX_TESTS_INNER_RUN for every pytest they launch; nothing started
# from inside such a run may start this lane again.
if [[ -n "${PODEX_TESTS_INNER_RUN:-}" ]]; then
	echo "PODEX_TESTS_INNER_RUN is set: refusing to start the PostgreSQL lane from inside a test-launched run" >&2
	exit 1
fi

if [[ -z "${PODEX_DATABASE__URL:-}" ]]; then
	echo "PODEX_DATABASE__URL must be set to the Postgres service URL" >&2
	exit 1
fi

case "${PODEX_DATABASE__URL}" in
postgresql*) ;;
*)
	echo "PODEX_DATABASE__URL must be a postgresql URL, got: ${PODEX_DATABASE__URL}" >&2
	exit 1
	;;
esac

cd "$(dirname "${BASH_SOURCE[0]}")/../../backend"

echo "::group::Install dependencies"
uv sync
echo "::endgroup::"

echo "::group::PostgreSQL-marked tests"
uv run pytest tests -m postgres --require-postgres -ra
echo "::endgroup::"
