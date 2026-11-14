"""Run a test target in a fresh interpreter and capture what it prints.

Shared by the tests that need a process of their own (entrypoints, exception
hooks). Imports only the standard library and pytest, so a spawned child
importing a target from here loads nothing else.
"""

import multiprocessing
import threading
from collections.abc import Callable

import pytest

DEFAULT_TIMEOUT_SECONDS = 120.0


def run_in_fresh_process(
    target: Callable[..., None],
    args: tuple[object, ...],
    capfd: pytest.CaptureFixture[str],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Run ``target(*args)`` in a spawned interpreter and return its output.

    The child inherits this process's environment and standard streams. A
    child still running after ``timeout`` seconds is killed; whatever
    happens, it is joined before this returns or raises.

    Args:
        target: Importable function run in the child.
        args: Arguments passed to ``target``.
        capfd: Captures the child's standard output and error.
        timeout: Seconds to wait for the child.

    Returns:
        tuple[str, str]: The child's standard output and standard error.

    Raises:
        AssertionError: When the child times out or exits unsuccessfully; the
            message includes everything it printed.
    """
    capfd.readouterr()
    process = multiprocessing.get_context("spawn").Process(target=target, args=args)
    timed_out = False
    try:
        process.start()
        process.join(timeout=timeout)
        timed_out = process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
        process.join()
    captured = capfd.readouterr()
    if timed_out or process.exitcode != 0:
        outcome = (
            f"timed out after {timeout:g}s"
            if timed_out
            else f"exited with {process.exitcode}"
        )
        raise AssertionError(
            f"child process {outcome}\n"
            f"--- stdout ---\n{captured.out}\n--- stderr ---\n{captured.err}",
        )
    return captured.out, captured.err


def wait_forever(*_args: object) -> None:
    """Block until killed: a child that never finishes on its own."""
    print("child started", flush=True)
    threading.Event().wait()
