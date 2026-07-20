#!/bin/sh
# =============================================================================
# Podex backend — container entrypoint
# -----------------------------------------------------------------------------
# Optionally runs Alembic migrations before exec-ing the service command.
#
#   AUTO_MIGRATE=1 (or "true")  -> run `alembic upgrade head` first
#   AUTO_MIGRATE unset / other  -> skip migrations (default; operators run
#                                  `alembic upgrade head` out-of-band)
#
# Every service built from this image goes through this entrypoint, so a
# command override (e.g. the scheduler's `python -m podex.scheduler_runner`)
# honors the same AUTO_MIGRATE contract. POSIX sh only — the runtime image
# ships no bash.
# =============================================================================
set -eu

case "${AUTO_MIGRATE:-0}" in
1 | true | TRUE | True)
	echo "AUTO_MIGRATE enabled: running alembic upgrade head" >&2
	alembic upgrade head
	;;
esac

exec "$@"
