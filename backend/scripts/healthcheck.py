#!/usr/bin/env python3
"""Probe the backend container according to the role of its own main process.

The role is ``PODEX_PROCESS_ROLE`` (``api`` or ``scheduler``) when that is set
in the container environment. Otherwise it is read from the command line of
PID 1, unwrapping the programs that start the real command without changing
its role: init processes (tini, docker-init, dumb-init), the image entrypoint
before it execs, ``env``, and ``sh``/``bash -c`` scripts. A scheduler is
``python -m podex.scheduler_runner`` with any interpreter options. Other
processes are never read, so a scheduler started with ``docker exec``, a
``pgrep -f`` for the module, or a scheduler in a shared PID namespace cannot
exempt the API from its HTTP check.

The API role passes only when exact ``/health`` on port 8000 returns HTTP 200.
The scheduler role passes without a network request; it does not prove
scheduler progress. Any failure to decide or probe exits with status 1.
"""

import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum, auto
from http.client import HTTPConnection, HTTPException
from pathlib import Path

ROLE_SETTING = "PODEX_PROCESS_ROLE"
SCHEDULER_MODULE = "podex.scheduler_runner"
ENTRYPOINT = "/app/scripts/start.sh"

_INIT_PROGRAMS = frozenset({"tini", "docker-init", "dumb-init"})
# tini -p SIGNAL / -e CODE and dumb-init -r FROM:TO take a separate value.
_INIT_VALUE_OPTIONS = frozenset({"-p", "-e", "-r", "--rewrite"})
_SHELLS = frozenset({"sh", "dash", "ash", "bash"})
_ENV_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})
_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
# Python options whose value may be the following argument (-W, -X).
_PYTHON_VALUE_FLAGS = frozenset("WX")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*", re.DOTALL)
_SHELL_OPERATOR = re.compile(r"[();<>|&]+")


class Role(StrEnum):
    """What the container's main process serves."""

    API = auto()
    SCHEDULER = auto()


def _program(argument: str) -> str:
    """Return the file name of a command path."""
    return argument.rsplit("/", 1)[-1]


def _python_runs_scheduler(arguments: Sequence[str]) -> bool:
    """Tell whether interpreter arguments select the scheduler module.

    Args:
        arguments: Arguments after the interpreter path.

    Returns:
        Whether ``-m`` names the scheduler module before any script or ``-c``.
    """
    remaining = list(arguments)
    while remaining:
        argument = remaining.pop(0)
        if argument == "--check-hash-based-pycs":
            remaining = remaining[1:]
            continue
        if argument in {"-", "--"} or not argument.startswith("-"):
            return False
        if argument.startswith("--"):
            continue
        flags = argument[1:]
        for index, flag in enumerate(flags):
            value = flags[index + 1 :]
            if flag == "c":
                return False
            if flag == "m":
                module = value or (remaining[0] if remaining else "")
                return module == SCHEDULER_MODULE
            if flag in _PYTHON_VALUE_FLAGS:
                if not value:
                    remaining = remaining[1:]
                break
    return False


def _after_options(
    arguments: Sequence[str],
    value_options: frozenset[str],
    *,
    skip_assignments: bool = False,
) -> list[str]:
    """Return the wrapped command after a wrapper's own options.

    Args:
        arguments: Arguments after the wrapper program.
        value_options: Options that consume the following argument.
        skip_assignments: Whether ``NAME=VALUE`` arguments are also skipped.

    Returns:
        The command the wrapper runs.
    """
    remaining = list(arguments)
    while remaining:
        argument = remaining[0]
        if argument == "--":
            return remaining[1:]
        if argument in value_options:
            remaining = remaining[2:]
        elif argument.startswith("-") or (
            skip_assignments and _ASSIGNMENT.fullmatch(argument)
        ):
            remaining = remaining[1:]
        else:
            break
    return remaining


def _shell_runs_scheduler(arguments: Sequence[str]) -> bool:
    """Tell whether shell arguments run the scheduler.

    Args:
        arguments: Arguments after the shell path.

    Returns:
        Whether a ``-c`` script runs the scheduler, or the image entrypoint
        script is about to exec it.
    """
    index = 0
    has_script = False
    while (
        index < len(arguments)
        and arguments[index].startswith("-")
        and arguments[index] not in {"-", "--"}
    ):
        has_script = has_script or (
            not arguments[index].startswith("--") and "c" in arguments[index]
        )
        index += 1
    if index >= len(arguments):
        return False
    if has_script:
        return script_runs_scheduler(arguments[index])
    if arguments[index] == ENTRYPOINT:
        return command_runs_scheduler(arguments[index + 1 :])
    return False


def script_runs_scheduler(script: str) -> bool:
    """Tell whether any simple command of a shell script runs the scheduler.

    Args:
        script: The ``-c`` script of a shell.

    Returns:
        Whether a command, after ``exec`` and variable assignments, runs the
        scheduler. A script that cannot be tokenized does not.
    """
    lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return False
    command: list[str] = []
    for token in [*tokens, ";"]:
        if not _SHELL_OPERATOR.fullmatch(token):
            command.append(token)
            continue
        while command and (command[0] == "exec" or _ASSIGNMENT.fullmatch(command[0])):
            command.pop(0)
        if command_runs_scheduler(command):
            return True
        command = []
    return False


def command_runs_scheduler(argv: Sequence[str]) -> bool:
    """Tell whether a command line runs the scheduler, unwrapping launchers.

    Args:
        argv: Command line, program first.

    Returns:
        Whether the command, or the command its launcher runs, is the
        scheduler module under Python.
    """
    if not argv:
        return False
    program, arguments = _program(argv[0]), argv[1:]
    if argv[0] == ENTRYPOINT:
        wrapped = list(arguments)
    elif program in _INIT_PROGRAMS:
        wrapped = _after_options(arguments, _INIT_VALUE_OPTIONS)
    elif program == "env":
        wrapped = _after_options(arguments, _ENV_VALUE_OPTIONS, skip_assignments=True)
    elif program in _SHELLS:
        return _shell_runs_scheduler(arguments)
    else:
        return bool(_PYTHON.fullmatch(program)) and _python_runs_scheduler(arguments)
    return command_runs_scheduler(wrapped)


def detected_role(proc_root: Path) -> Role:
    """Read the role from the command line of PID 1.

    Args:
        proc_root: Linux process filesystem of the container.

    Returns:
        The scheduler role when PID 1 runs it, otherwise the API role,
        including when PID 1 cannot be read.
    """
    try:
        raw = (proc_root / "1" / "cmdline").read_bytes()
    except OSError:
        return Role.API
    argv = [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0")]
    if argv[-1] == "":
        argv.pop()
    return Role.SCHEDULER if command_runs_scheduler(argv) else Role.API


def process_role(proc_root: Path, environ: Mapping[str, str]) -> Role:
    """Return the configured role, or the role PID 1 shows.

    Args:
        proc_root: Linux process filesystem of the container.
        environ: Environment of the probe, which Docker copies from the
            container.

    Returns:
        The role to probe.

    Raises:
        ValueError: The role setting names no known role.
    """
    configured = environ.get(ROLE_SETTING, "").strip().lower()
    if not configured:
        return detected_role(proc_root)
    return Role(configured)


def api_serves_health() -> bool:
    """Request exact ``/health`` from the default API port.

    Returns:
        Whether the API answered HTTP 200.
    """
    connection = HTTPConnection("127.0.0.1", 8000, timeout=2)
    try:
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def is_healthy(
    proc_root: Path = Path("/proc"),
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Check the container according to its role.

    Args:
        proc_root: Linux process filesystem of the container.
        environ: Environment holding the optional role setting; defaults to
            this process's environment.

    Returns:
        True for the scheduler role; for the API role, whether exact
        ``/health`` serves HTTP 200. An invalid role setting is unhealthy.
    """
    try:
        role = process_role(proc_root, os.environ if environ is None else environ)
    except ValueError:
        print(
            f"{ROLE_SETTING} must be one of: {', '.join(Role)}",
            file=sys.stderr,
        )
        return False
    if role is Role.SCHEDULER:
        return True
    return api_serves_health()


def main() -> int:
    """Run the probe.

    Returns:
        0 when healthy, otherwise 1.
    """
    try:
        return 0 if is_healthy() else 1
    except (OSError, ValueError, HTTPException):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
