"""Child process: the migration environment, then a migration failure.

Run in a fresh interpreter by ``tests/test_log_boundary_entrypoints.py``. The
parent points the environment at an unreachable PostgreSQL database: offline
SQL generation needs no connection, while SQLite's batch migrations would.
"""

import io
import logging
import os
import sys
from pathlib import Path

from alembic.command import upgrade
from alembic.config import Config

from tests.database_error_support import print_uncaught, unique_violation

_BACKEND = Path(__file__).resolve().parent.parent


def run(row_value: str) -> None:
    """Load the migration environment, then log and end with a database error.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    os.chdir(_BACKEND)
    upgrade(Config("alembic.ini", output_buffer=io.StringIO()), "head", sql=True)
    print("podex.main imported:", "podex.main" in sys.modules, flush=True)
    error = unique_violation(row_value)
    logging.getLogger("alembic.runtime.migration").error(
        "migration failed",
        exc_info=error,
    )
    print_uncaught(error)
