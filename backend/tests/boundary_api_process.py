"""Child process: the API app factory, then a server-side failure.

Run in a fresh interpreter by ``tests/test_log_boundary_entrypoints.py``. The
parent sets the environment before the child starts.
"""

import logging

from podex.main import create_app
from tests.database_error_support import print_uncaught, unique_violation


def run(row_value: str) -> None:
    """Build the API app, then log and end with a database error.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    create_app()
    error = unique_violation(row_value)
    logging.getLogger("uvicorn.error").error(
        "Exception in ASGI application",
        exc_info=error,
    )
    print_uncaught(error)
