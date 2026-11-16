"""Child processes: the scheduler entrypoint, with one successful or failed tick.

Run in a fresh interpreter by ``tests/test_log_boundary_entrypoints.py``. The
parent sets the environment (database URL, tick interval of 0, reporting
off) before the child starts, so importing the runner here reads it.
"""

import signal
import sys
from unittest.mock import patch

from podex import scheduler_runner
from podex.database import engine
from podex.models import Base
from tests.database_error_support import unique_violation


def run(row_value: str) -> None:
    """Run the scheduler entrypoint for one failing tick, then report imports.

    Args:
        row_value: The row value the database error's driver message reveals.
    """

    def failing_tick(**_kwargs: object) -> object:
        """Ask the runner to stop after this tick, then fail as a write would."""
        signal.raise_signal(signal.SIGTERM)
        raise unique_violation(row_value)

    scheduler_runner.run_instrumented_tick = failing_tick
    scheduler_runner.main()
    print("podex.main imported:", "podex.main" in sys.modules, flush=True)
    sys.stderr.flush()


def run_one_tick(row_value: str) -> None:
    """Run a real SQLite tick, then log a database error and report imports.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    Base.metadata.create_all(bind=engine)

    def stop_after_tick(_runner: scheduler_runner.SchedulerRunner) -> None:
        """Stop through the installed signal handler instead of sleeping."""
        signal.raise_signal(signal.SIGTERM)

    with patch.object(
        scheduler_runner.SchedulerRunner,
        "_sleep_until_next_tick",
        stop_after_tick,
    ):
        scheduler_runner.main()
    scheduler_runner.logger.error("database failure: %s", unique_violation(row_value))
    print("podex.main imported:", "podex.main" in sys.modules, flush=True)
    sys.stderr.flush()
