"""Run a test target in a fresh interpreter and capture what it prints.

Shared by the tests that need a process of their own (entrypoints, exception
hooks, start-up commands). Imports only the standard library and pytest, so a
spawned child importing a target from here loads nothing else.
"""

import asyncio
import multiprocessing
import threading
from asyncio.subprocess import PIPE
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a finished command printed and how it exited.

    Attributes:
        returncode: The command's exit status.
        stdout: Everything it wrote to standard output.
        stderr: Everything it wrote to standard error.
    """

    returncode: int
    stdout: str
    stderr: str

    def describe(self) -> str:
        """Return the exit status and output, for assertion messages."""
        return (
            f"exited with {self.returncode}\n"
            f"--- stdout ---\n{self.stdout}\n--- stderr ---\n{self.stderr}"
        )


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run a command in a new process and return what it printed.

    Unlike :func:`run_in_fresh_process`, a failing exit status is returned,
    not raised: start-up failures are what the callers examine. A command
    still running after ``timeout`` seconds is killed and waited for.

    Args:
        argv: The program and its arguments.
        env: The command's whole environment.
        cwd: Its working directory.
        timeout: Seconds to wait for it.

    Returns:
        CommandResult: Its exit status and output, decoded with replacement.

    Raises:
        AssertionError: When the command times out; the message includes
            everything it printed.
    """
    returncode, stdout, stderr, timed_out = asyncio.run(
        _run_bounded(argv, env=env, cwd=cwd, timeout=timeout),
    )
    if timed_out:
        raise AssertionError(
            f"command timed out after {timeout:g}s\n"
            f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
        )
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


async def _run_bounded(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
) -> tuple[int, str, str, bool]:
    """Run the command, killing it after ``timeout``; keep all it printed.

    Returns:
        tuple[int, str, str, bool]: Exit status, standard output, standard
        error and whether it timed out.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env),
        cwd=cwd,
        stdout=PIPE,
        stderr=PIPE,
    )
    if process.stdout is None or process.stderr is None:
        raise AssertionError("command output was not captured")
    # Read both streams while waiting, so a full pipe cannot stall the command
    # and output written before a timeout is kept.
    readers = (
        asyncio.ensure_future(process.stdout.read()),
        asyncio.ensure_future(process.stderr.read()),
    )
    timed_out = False
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except TimeoutError:
        timed_out = True
    finally:
        if process.returncode is None:
            process.kill()
        returncode = await process.wait()
    stdout, stderr = await asyncio.gather(*readers)
    return (
        returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        timed_out,
    )
