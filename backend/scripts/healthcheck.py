#!/usr/bin/env python3
"""Probe the backend container according to the role of its own main process.

The role is ``PODEX_PROCESS_ROLE`` (``api`` or ``scheduler``) when that is set
in the container environment. Otherwise it is read from the argument vector of
PID 1 with a closed rule; shell scripts are never interpreted. After removing
up to four recognised launchers (the image entrypoint, tini, docker-init or
dumb-init with their documented options, ``env`` with ``NAME=value``
assignments only), the container is the scheduler only when what remains is::

    python [-u|-B|-O|-OO ...] -m podex.scheduler_runner [arguments]

or ``sh``/``bash``/``dash``/``ash`` ``-c`` with a script that is exactly that
invocation, optionally preceded by ``exec``, without any shell metacharacter.
Anything else, including compound scripts, script files, ``uv run`` and
shared PID namespaces, gets the API's HTTP check; such scheduler deployments
set ``PODEX_PROCESS_ROLE=scheduler``. Other processes are never read, so a
scheduler started with ``docker exec`` cannot exempt an API container.

The API role passes only when exact ``/health`` on port 8000 returns HTTP 200.
The scheduler role passes without a network request; it does not prove
scheduler progress. Reading is bounded, an undecidable command line gets the
HTTP check, and any unexpected error exits with status 1.
"""

import os
import re
import sys
from collections.abc import Mapping, Sequence
from enum import StrEnum, auto
from http.client import HTTPConnection, HTTPException
from pathlib import Path

ROLE_SETTING = "PODEX_PROCESS_ROLE"
SCHEDULER_MODULE = "podex.scheduler_runner"
ENTRYPOINT = "/app/scripts/start.sh"
# The entrypoint's shebang; the kernel runs it as `/bin/sh <entrypoint> ...`.
ENTRYPOINT_SHELL = "/bin/sh"

# A real command line is far shorter; larger ones are not decided.
MAX_CMDLINE_BYTES = 16 * 1024
MAX_ARGUMENTS = 64
MAX_LAUNCHERS = 4

_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
_SAFE_PYTHON_FLAGS = frozenset({"-u", "-B", "-O", "-OO"})
_SHELLS = frozenset({"sh", "bash", "dash", "ash"})
# Any of these makes a `-c` script more than one plain command.
_SHELL_METACHARACTERS = frozenset(";&|<>()$`\n\"'\\#*?[]{}~!")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=[^\0]*")
# tini (also docker-init) and dumb-init options that may precede the command.
_INIT_FLAGS = {
    "tini": frozenset({"-s", "-v", "-vv", "-vvv", "-w", "-g"}),
    "docker-init": frozenset({"-s", "-v", "-vv", "-vvv", "-w", "-g"}),
    "dumb-init": frozenset({"-c", "--single-child", "-v", "--verbose"}),
}
_INIT_VALUE_OPTIONS = {
    "tini": frozenset({"-p", "-e"}),
    "docker-init": frozenset({"-p", "-e"}),
    "dumb-init": frozenset({"-r", "--rewrite"}),
}


class Role(StrEnum):
    """What the container's main process serves."""

    API = auto()
    SCHEDULER = auto()


class RoleUndecidedError(Exception):
    """The role could not be decided from PID 1."""


def _program(argument: str) -> str:
    """Return the file name of a command path."""
    return argument.rsplit("/", 1)[-1]


def _is_scheduler_python(argv: Sequence[str], start: int) -> bool:
    """Tell whether ``argv[start:]`` is the plain scheduler invocation.

    Args:
        argv: Command line.
        start: Index of the interpreter.

    Returns:
        Whether it is a Python interpreter, safe flags, then
        ``-m podex.scheduler_runner``. Later arguments are not interpreted.
    """
    if start >= len(argv) or not _PYTHON.fullmatch(_program(argv[start])):
        return False
    index = start + 1
    while index < len(argv) and argv[index] in _SAFE_PYTHON_FLAGS:
        index += 1
    return list(argv[index : index + 2]) == ["-m", SCHEDULER_MODULE]


def _is_scheduler_script(script: str) -> bool:
    """Tell whether a ``-c`` script is exactly one plain scheduler command.

    Args:
        script: The shell script.

    Returns:
        Whether the script, without surrounding whitespace, is the scheduler
        invocation optionally preceded by ``exec`` and has no metacharacter.
    """
    stripped = script.strip()
    if any(character in _SHELL_METACHARACTERS for character in stripped):
        return False
    words = stripped.split()
    start = 1 if words[:1] == ["exec"] else 0
    return _is_scheduler_python(words, start)


def _after_init(argv: Sequence[str], start: int) -> int | None:
    """Return the index of the command an init process runs.

    Args:
        argv: Command line.
        start: Index of the init program.

    Returns:
        Index after the documented options and an optional ``--``, or None
        when an argument is not one of those options.
    """
    program = _program(argv[start])
    flags, value_options = _INIT_FLAGS[program], _INIT_VALUE_OPTIONS[program]
    index = start + 1
    while index < len(argv):
        argument = argv[index]
        if argument == "--":
            return index + 1
        if argument in flags:
            index += 1
        elif argument in value_options:
            index += 2
        elif program == "dumb-init" and argument.startswith("--rewrite="):
            index += 1
        elif argument.startswith("-"):
            return None
        else:
            return index
    return None


def _after_env(argv: Sequence[str], start: int) -> int:
    """Return the index of the command ``env`` runs after its assignments.

    Args:
        argv: Command line.
        start: Index of ``env``.

    Returns:
        Index of the first argument that is not ``NAME=value``.
    """
    index = start + 1
    while index < len(argv) and _ASSIGNMENT.fullmatch(argv[index]):
        index += 1
    return index


def _unwrap(argv: Sequence[str]) -> int | None:
    """Skip recognised launchers at the start of PID 1's command line.

    Args:
        argv: Command line.

    Returns:
        Index of the command they run, or None when undecidable.
    """
    index = 0
    for _ in range(MAX_LAUNCHERS + 1):
        if index >= len(argv):
            return None
        program = _program(argv[index])
        if argv[index] == ENTRYPOINT:
            index += 1
        elif argv[index] == ENTRYPOINT_SHELL and list(argv[index + 1 : index + 2]) == [
            ENTRYPOINT
        ]:
            index += 2
        elif program in _INIT_FLAGS:
            after = _after_init(argv, index)
            if after is None:
                return None
            index = after
        elif program == "env":
            index = _after_env(argv, index)
        else:
            return index
    return None


def command_runs_scheduler(argv: Sequence[str]) -> bool:
    """Tell whether PID 1's argument vector runs the scheduler.

    Args:
        argv: Command line, program first.

    Returns:
        Whether it matches one of the supported scheduler forms.
    """
    if len(argv) > MAX_ARGUMENTS:
        return False
    index = _unwrap(argv)
    if index is None:
        return False
    if _program(argv[index]) in _SHELLS:
        return (
            len(argv) == index + 3
            and argv[index + 1] == "-c"
            and _is_scheduler_script(argv[index + 2])
        )
    return _is_scheduler_python(argv, index)


def _read_pid1(proc_root: Path) -> list[str] | None:
    """Read PID 1's argument vector, bounded in size.

    Args:
        proc_root: Linux process filesystem of the container.

    Returns:
        The arguments, or None when unreadable or too large.
    """
    try:
        with (proc_root / "1" / "cmdline").open("rb") as command_line:
            raw = command_line.read(MAX_CMDLINE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_CMDLINE_BYTES:
        return None
    argv = [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0")]
    if argv[-1] == "":
        argv.pop()
    return argv


def _decide(proc_root: Path) -> Role:
    """Decide the role from PID 1, translating any failure.

    Args:
        proc_root: Linux process filesystem of the container.

    Returns:
        The decided role.

    Raises:
        RoleUndecidedError: The command line could not be read or parsed.
    """
    try:
        argv = _read_pid1(proc_root)
        if argv is not None and command_runs_scheduler(argv):
            return Role.SCHEDULER
    except Exception as error:
        raise RoleUndecidedError from error
    return Role.API


def detected_role(proc_root: Path) -> Role:
    """Read the role from the command line of PID 1.

    Args:
        proc_root: Linux process filesystem of the container.

    Returns:
        The scheduler role when PID 1 runs it in a supported form, otherwise
        the API role, including when PID 1 cannot be read or parsed.
    """
    try:
        return _decide(proc_root)
    except RoleUndecidedError:
        return Role.API


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
    """Run the probe; no exception escapes it.

    Returns:
        0 when healthy, otherwise 1.

    Raises:
        SystemExit: Status 1 after an unexpected error, reported on one line.
    """
    try:
        healthy = is_healthy()
    except Exception as error:
        print(f"health probe error: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
