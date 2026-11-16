"""Child processes: the scheduler entrypoint, with one successful or failed tick.

Run in a fresh interpreter by ``tests/test_log_boundary_entrypoints.py``. The
parent sets the database URL and disables reporting before the child starts.
The module command uses a 30-second interval, so a second tick cannot happen
before the parent's stop signal lands, and stops after its first tick.
"""

import asyncio
import logging
import os
import signal
import sys
from asyncio.subprocess import PIPE, Process
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from podex.database import engine
from podex.models import Base
from tests.database_error_support import unique_violation
from tests.sdk_log_support import log_sdk_request


def run(row_value: str) -> None:
    """Run the scheduler for one failing tick, log an SDK request, report imports.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    # The module-command startup hook also imports this helper, so only
    # load the runner here for the in-process failure case.
    scheduler_runner = import_module("podex.scheduler_runner")

    def failing_tick(**_kwargs: object) -> object:
        """Ask the runner to stop after this tick, then fail as a write would."""
        signal.raise_signal(signal.SIGTERM)
        raise unique_violation(row_value)

    with patch.object(scheduler_runner, "run_instrumented_tick", failing_tick):
        scheduler_runner.main()
    log_sdk_request(row_value)
    print("podex.main imported:", "podex.main" in sys.modules, flush=True)
    sys.stderr.flush()


def run_one_tick(row_value: str) -> None:
    """Run the documented module command for one real SQLite tick.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    Base.metadata.create_all(bind=engine)
    with TemporaryDirectory(prefix="scheduler-module-") as directory:
        # Register exit checks before -m runs; its logging setup sanitises them.
        hook = Path(directory) / "sitecustomize.py"
        hook.write_text(
            "from atexit import register\n"
            "from tests.boundary_scheduler_process import report_module_process\n"
            f"register(report_module_process, {row_value!r})\n",
            encoding="utf-8",
        )
        environment = dict(
            os.environ,
            PODEX_SCHEDULER_TICK_SECONDS="30",
            PYTHONPATH=os.pathsep.join(
                (
                    directory,
                    str(Path(__file__).resolve().parent.parent),
                    os.environ.get("PYTHONPATH", ""),
                ),
            ),
        )
        asyncio.run(_run_module(environment))


async def _run_module(environment: dict[str, str]) -> None:
    """Launch the module with a bounded wait and kill it on timeout."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "podex.scheduler_runner",
        env=environment,
        stderr=PIPE,
    )
    try:
        await asyncio.wait_for(_stop_after_tick(process), timeout=30)
    finally:
        if process.returncode is None:
            process.kill()
        _stdout, remaining = await process.communicate()
        if remaining:
            sys.stderr.write(remaining.decode(errors="replace"))
            sys.stderr.flush()


async def _stop_after_tick(process: Process) -> None:
    """Forward stderr and signal the process immediately after its first tick."""
    stderr = process.stderr
    if stderr is None:
        raise AssertionError("scheduler stderr was not captured")
    stopping = False
    while line := await stderr.readline():
        sys.stderr.write(line.decode())
        sys.stderr.flush()
        if b"scheduler_tick planned=" in line and not stopping:
            process.send_signal(signal.SIGTERM)
            stopping = True
    returncode = await process.wait()
    if returncode != 0:
        raise AssertionError(f"scheduler module exited with {returncode}")


def report_module_process(row_value: str) -> None:
    """Log a database error and report imports as the module process exits.

    Args:
        row_value: The row value the database error's driver message reveals.
    """
    logging.getLogger("podex.scheduler_runner").error(
        "database failure: %s",
        unique_violation(row_value),
    )
    print("podex.main imported:", "podex.main" in sys.modules, flush=True)
    sys.stderr.flush()
