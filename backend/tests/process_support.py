"""Run a test target in a fresh interpreter and capture what it prints.

Shared by the tests that need a process of their own (entrypoints, exception
hooks, start-up commands). Imports only the standard library and pytest, so a
spawned child importing a target from here loads nothing else.
"""

import asyncio
import contextlib
import multiprocessing
import os
import signal
import threading
from asyncio.subprocess import PIPE, Process
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


DRAIN_TIMEOUT_SECONDS = 5.0
"""Seconds to keep reading output after a command ends.

A process the command started outside its session can inherit its pipes and
hold them open.
"""


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ready: str | None = None,
) -> CommandResult:
    """Run a command in a new process and return what it printed.

    Unlike :func:`run_in_fresh_process`, a failing exit status is returned,
    not raised: start-up failures are what the callers examine. A command
    still running after ``timeout`` seconds is killed and waited for. With
    ``ready``, the ``timeout`` starts once the command has written ``ready``
    to standard output (or has exited); that wait is bounded by
    :data:`DEFAULT_TIMEOUT_SECONDS`.

    Args:
        argv: The program and its arguments.
        env: The command's whole environment.
        cwd: Its working directory.
        timeout: Seconds to wait for it.
        ready: Output that starts the ``timeout``, if any.

    Returns:
        CommandResult: Its exit status and output, decoded with replacement.

    Raises:
        AssertionError: When the command times out or never prints ``ready``;
            the message includes everything it printed.
    """
    returncode, stdout, stderr, failure = asyncio.run(
        _run_bounded(argv, env=env, cwd=cwd, timeout=timeout, ready=ready),
    )
    if failure is not None:
        raise AssertionError(
            f"command {failure}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
        )
    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


async def _drain(
    stream: asyncio.StreamReader,
    sink: bytearray,
    *,
    marker: bytes = b"",
    seen: asyncio.Event | None = None,
) -> None:
    """Append everything ``stream`` yields to ``sink``; flag ``marker`` once seen."""
    while chunk := await stream.read(65_536):
        sink.extend(chunk)
        if seen is not None and marker in sink:
            seen.set()


_EXIT_POLL_SECONDS = 0.05


async def _exited(process: Process) -> None:
    """Return once ``process`` has exited, whether or not its pipes are open.

    ``Process.wait`` also waits for the pipes to close, which a process the
    command started can hold open indefinitely.
    """
    while process.returncode is None:
        await asyncio.sleep(_EXIT_POLL_SECONDS)


async def _ready_or_exited(process: Process, seen: asyncio.Event) -> None:
    """Return once ``seen`` is set or ``process`` has exited."""
    waiters = {
        asyncio.ensure_future(seen.wait()),
        asyncio.ensure_future(_exited(process)),
    }
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


async def _run_bounded(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
    ready: str | None,
) -> tuple[int, str, str, str | None]:
    """Run the command within its bounds; keep all it printed.

    The command runs in a session of its own; whatever it started there is
    killed when it ends.

    Returns:
        tuple[int, str, str, str | None]: Exit status, standard output,
        standard error and why the command was stopped, if it was.
    """
    process = await asyncio.create_subprocess_exec(
        *argv,
        env=dict(env),
        cwd=cwd,
        stdout=PIPE,
        stderr=PIPE,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        raise AssertionError("command output was not captured")
    stdout, stderr = bytearray(), bytearray()
    seen = asyncio.Event()
    # Read both streams while waiting, so a full pipe cannot stall the command
    # and output written before a timeout is kept.
    readers = {
        asyncio.ensure_future(
            _drain(process.stdout, stdout, marker=(ready or "").encode(), seen=seen),
        ),
        asyncio.ensure_future(_drain(process.stderr, stderr)),
    }
    failure: str | None = None
    try:
        if ready is not None:
            try:
                await asyncio.wait_for(
                    _ready_or_exited(process, seen),
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                failure = f"did not print {ready!r} within {DEFAULT_TIMEOUT_SECONDS:g}s"
        if failure is None:
            try:
                await asyncio.wait_for(_exited(process), timeout=timeout)
            except TimeoutError:
                failure = f"timed out after {timeout:g}s"
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(_exited(process), timeout=DRAIN_TIMEOUT_SECONDS)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        _done, pending = await asyncio.wait(readers, timeout=DRAIN_TIMEOUT_SECONDS)
        for reader in pending:
            reader.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    returncode = process.returncode
    if returncode is None:
        raise AssertionError("command exit status was not recorded")
    return (
        returncode,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
        failure,
    )
