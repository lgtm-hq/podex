#!/usr/bin/env bash
# =============================================================================
# Postgres migration replay (#299)
# -----------------------------------------------------------------------------
# Replays the full Alembic migration chain against the CI Postgres service and
# runs the migration test module as a smoke check. PODEX_DATABASE_URL must
# point at the Postgres service (alembic/env.py falls back to it when
# alembic.ini leaves sqlalchemy.url empty).
# =============================================================================
set -euo pipefail

if [[ -z "${PODEX_DATABASE_URL:-}" ]]; then
  echo "PODEX_DATABASE_URL must be set to the Postgres service URL" >&2
  exit 1
fi

case "${PODEX_DATABASE_URL}" in
  postgresql*) ;;
  *)
    echo "PODEX_DATABASE_URL must be a postgresql URL, got: ${PODEX_DATABASE_URL}" >&2
    exit 1
    ;;
esac

cd backend

echo "::group::Install dependencies"
uv sync
echo "::endgroup::"

echo "::group::Replay migration chain on Postgres"
uv run alembic upgrade head
uv run alembic current
echo "::endgroup::"

echo "::group::Migration test smoke run"
uv run pytest tests/test_migrations.py
echo "::endgroup::"
