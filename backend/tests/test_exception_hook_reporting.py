"""An uncaught database error is still reported, whichever hook came first.

The log boundary wraps the process exception hook; the error reporter wraps
it too. In both installation orders the reporter must receive the error
exactly once, without driver text, and the printed traceback must hold the
descriptor only. Each order runs in a fresh interpreter because the
reporter installs its hook once per process.
"""

import json
from secrets import token_hex

import pytest
from assertpy import assert_that

from tests import boundary_reporter_process
from tests.database_error_support import SQLSTATE_DESCRIPTOR
from tests.process_support import run_in_fresh_process


@pytest.mark.parametrize(
    "order",
    [
        boundary_reporter_process.REPORTER_FIRST,
        boundary_reporter_process.BOUNDARY_FIRST,
    ],
)
def test_uncaught_database_error_is_reported_once_without_driver_text(
    order: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """One event without the row value; the printed traceback is sanitised."""
    row_value = f"hook-row-{token_hex(8)}"

    stdout, stderr = run_in_fresh_process(
        boundary_reporter_process.run,
        (row_value, order),
        capfd,
    )

    result = json.loads(stdout.strip().splitlines()[-1])
    assert_that(result).is_equal_to({"events": 1, "leaked": False, "described": True})
    assert_that(stderr).contains(SQLSTATE_DESCRIPTOR)
    assert_that(stderr).does_not_contain(row_value)
