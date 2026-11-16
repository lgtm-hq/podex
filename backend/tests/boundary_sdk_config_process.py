"""Child process: the SDK boundary after repeated setup and later log configs.

Run in a fresh interpreter by ``tests/test_sdk_log_boundary.py``. Logging is
configured as the API does, several times; then the migration
environment's ``fileConfig`` and the ASGI server's ``dictConfig`` replace
the handlers and levels. SDK records logged afterwards must still be
sanitised, and the record factory must wrap the boundary exactly once.
"""

import logging
import logging.config
from functools import partial
from pathlib import Path

from uvicorn.config import LOGGING_CONFIG

from podex.logging_config import configure_logging, install_log_boundaries
from tests.sdk_log_support import log_sdk_request

_BACKEND = Path(__file__).resolve().parent.parent


def _boundary_wrappers() -> int:
    """Count the boundary wrappers in the record factory chain."""
    count = 0
    factory = logging.getLogRecordFactory()
    while isinstance(factory, partial):
        if getattr(factory.func, "__name__", "") == "_sanitising_record":
            count += 1
        factory = factory.args[0]
    return count


def run(secret: str) -> None:
    """Configure logging repeatedly, reconfigure it, then log SDK records.

    Args:
        secret: Planted in the logged URLs.
    """
    for _ in range(3):
        configure_logging()
        install_log_boundaries()
    logging.config.fileConfig(
        _BACKEND / "alembic.ini",
        disable_existing_loggers=False,
    )
    logging.config.dictConfig(LOGGING_CONFIG)
    configure_logging()
    print("boundary wrappers:", _boundary_wrappers(), flush=True)
    print("httpx filters:", len(logging.getLogger("httpx").filters), flush=True)
    log_sdk_request(secret)
    logging.getLogger("uvicorn.access").warning(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5000",
        "GET",
        f"/api/v2/podcasts?token={secret}",
        "1.1",
        200,
    )
