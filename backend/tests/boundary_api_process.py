"""Child process: the API app factory, then a server-side failure.

Run in a fresh interpreter by ``tests/test_log_boundary_entrypoints.py``. The
parent sets the environment before the child starts.
"""

import logging

from podex.main import create_app
from tests.database_error_support import print_uncaught, unique_violation
from tests.sdk_log_support import log_sdk_request


def run(row_value: str) -> None:
    """Build the API app, log an SDK request, then end with a database error.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    create_app()
    log_sdk_request(row_value)
    error = unique_violation(row_value)
    logging.getLogger("uvicorn.error").error(
        "Exception in ASGI application",
        exc_info=error,
    )
    print_uncaught(error)
