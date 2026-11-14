"""Child process: an uncaught database error with the reporter's hook installed.

Run in a fresh interpreter by ``tests/test_exception_hook_reporting.py``,
because the reporter installs its process exception hook once per process.
The reporter and the log boundary are installed in the order given, envelopes
are kept in memory, and the result is printed as one JSON line.
"""

import json
import sys
from unittest.mock import patch

import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import HttpTransport

from podex.config import ObservabilitySettings, Settings
from podex.logging_config import install_log_boundaries
from podex.observability import init_sentry
from tests.database_error_support import SQLSTATE_DESCRIPTOR, unique_violation

REPORTER_FIRST = "reporter-first"
BOUNDARY_FIRST = "boundary-first"


def run(row_value: str, order: str) -> None:
    """Install both hooks in ``order``, end with a database error, report counts.

    Args:
        row_value: The row value the database error's driver message reveals.
        order: :data:`REPORTER_FIRST` or :data:`BOUNDARY_FIRST`.
    """
    envelopes: list[Envelope] = []

    def record(_transport: HttpTransport, envelope: Envelope) -> None:
        """Keep the envelope instead of sending it."""
        envelopes.append(envelope)

    with patch.object(HttpTransport, "capture_envelope", record):
        settings = Settings(
            observability=ObservabilitySettings(
                sentry_dsn="https://public@sentry.invalid/1",
            ),
        )
        if order == REPORTER_FIRST:
            init_sentry(settings)
            install_log_boundaries()
        else:
            install_log_boundaries()
            init_sentry(settings)
        error = unique_violation(row_value)
        sys.excepthook(type(error), error, error.__traceback__)
        sentry_sdk.flush()
        sys.stderr.flush()
    wire = "".join(envelope.serialize().decode("utf-8") for envelope in envelopes)
    events = sum(
        1 for envelope in envelopes for item in envelope.items if item.type == "event"
    )
    result = {
        "events": events,
        "leaked": row_value in wire,
        "described": SQLSTATE_DESCRIPTOR in wire,
    }
    print(json.dumps(result), flush=True)
