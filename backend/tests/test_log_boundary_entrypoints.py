"""Every process entrypoint installs the log boundary on its own.

Each test starts a fresh interpreter (the ``spawn`` start method), runs one
entrypoint there (API app factory, scheduler runner, migration
environment), fails with a database error whose driver message reveals a
row value, and searches everything the process wrote to its standard
streams. The child modules import only their own entrypoint, and the
scheduler and migration processes check that ``podex.main`` was never
imported, so they cannot rely on the API's logging setup.
"""

import multiprocessing
from collections.abc import Callable
from pathlib import Path
from secrets import token_hex

import pytest
from assertpy import assert_that

from tests import (
    boundary_api_process,
    boundary_migration_process,
    boundary_scheduler_process,
)
from tests.database_error_support import SQLSTATE_DESCRIPTOR
from tests.process_support import run_in_fresh_process, wait_forever

_NOT_IMPORTED = "podex.main imported: False"
_OFFLINE_POSTGRES = "postgresql+psycopg://podex@db.invalid:5432/podex"


def _run(
    target: Callable[[str], None],
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> tuple[str, str, str]:
    """Run ``target`` in a fresh interpreter and capture its streams.

    The child inherits the environment set here.

    Returns:
        tuple[str, str, str]: The row value, standard output and standard error.
    """
    row_value = f"entry-row-{token_hex(8)}"
    monkeypatch.setenv("PODEX_DATABASE__URL", database_url)
    monkeypatch.setenv("PODEX_OBSERVABILITY__SENTRY_DSN", "")
    monkeypatch.setenv("PODEX_SCHEDULER_TICK_SECONDS", "0")
    stdout, stderr = run_in_fresh_process(target, (row_value,), capfd)
    return row_value, stdout, stderr


def test_timed_out_child_is_killed_and_reported(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A child that never finishes is killed, joined and reported with output."""
    with pytest.raises(AssertionError) as caught:
        run_in_fresh_process(wait_forever, (), capfd, timeout=0.5)

    assert_that(str(caught.value)).contains("timed out after 0.5s")
    assert_that(str(caught.value)).contains("--- stderr ---")
    assert_that(multiprocessing.active_children()).is_empty()


def test_scheduler_process_withholds_database_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """A failed scheduler tick logs the descriptor, not the driver's DETAIL."""
    row_value, stdout, stderr = _run(
        boundary_scheduler_process.run,
        f"sqlite:///{tmp_path / 'scheduler.db'}",
        monkeypatch,
        capfd,
    )

    assert_that(stdout).contains(_NOT_IMPORTED)
    assert_that(stderr).contains("scheduler_tick_failed")
    assert_that(stderr).contains(SQLSTATE_DESCRIPTOR)
    assert_that(stdout + stderr).does_not_contain(row_value)


def test_migration_process_withholds_database_text(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Migration logs and an uncaught migration error print no DETAIL."""
    row_value, stdout, stderr = _run(
        boundary_migration_process.run,
        _OFFLINE_POSTGRES,
        monkeypatch,
        capfd,
    )

    assert_that(stdout).contains(_NOT_IMPORTED)
    assert_that(stderr).contains("migration failed")
    assert_that(stderr).contains(SQLSTATE_DESCRIPTOR)
    assert_that(stdout + stderr).does_not_contain(row_value)


def test_api_process_withholds_database_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The ASGI server's error log and an uncaught error print no DETAIL."""
    row_value, stdout, stderr = _run(
        boundary_api_process.run,
        f"sqlite:///{tmp_path / 'api.db'}",
        monkeypatch,
        capfd,
    )

    assert_that(stderr).contains("Exception in ASGI application")
    assert_that(stderr).contains(SQLSTATE_DESCRIPTOR)
    assert_that(stdout + stderr).does_not_contain(row_value)
