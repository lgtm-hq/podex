"""Exercise the container probe's role decision and API liveness check."""

import time
from http.client import HTTPConnection, HTTPException
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from scripts import healthcheck

MODULE = "podex.scheduler_runner"
SCHEDULER = ["python", "-m", MODULE]
API = ["uvicorn", "podex.main:app", "--port", "8000", "--no-access-log"]


def _cmdline(argv: list[str]) -> bytes:
    """Encode an argument vector the way /proc/<pid>/cmdline stores it."""
    return b"".join(argument.encode() + b"\0" for argument in argv)


def _process_table(root: Path, commands: dict[int, bytes]) -> Path:
    """Write a fake ``/proc`` holding one command line per process id."""
    for process_id, command in commands.items():
        process = root / str(process_id)
        process.mkdir()
        (process / "cmdline").write_bytes(command)
    return root


@pytest.fixture(name="http")
def fixture_http(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace the HTTP client with one whose /health fails, and return it."""
    connection = MagicMock(spec=HTTPConnection)
    connection.request.side_effect = ConnectionRefusedError("API down")
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(healthcheck, "HTTPConnection", factory)
    return factory


def _assert_scheduler(pid1: bytes, root: Path, http: MagicMock) -> None:
    """PID 1 decides the scheduler role and the HTTP check is skipped."""
    proc = _process_table(root, {1: pid1})
    assert_that(healthcheck.detected_role(proc)).is_equal_to(healthcheck.Role.SCHEDULER)
    assert_that(healthcheck.is_healthy(proc_root=proc, environ={})).is_true()
    assert_that(http.called).is_false()


def _assert_api(pid1: bytes, root: Path, http: MagicMock) -> None:
    """PID 1 decides the API role and the HTTP check runs (and fails)."""
    proc = _process_table(root, {1: pid1})
    assert_that(healthcheck.detected_role(proc)).is_equal_to(healthcheck.Role.API)
    assert_that(healthcheck.is_healthy(proc_root=proc, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


SCHEDULER_FORMS = {
    "exec-form": SCHEDULER,
    "venv-interpreter": ["/app/.venv/bin/python3.15", "-m", MODULE],
    "python3": ["python3", "-m", MODULE],
    "python-u-m": ["python", "-u", "-m", MODULE],
    "safe-flags": ["python", "-B", "-O", "-OO", "-u", "-m", MODULE],
    "module-arguments-ignored": [*SCHEDULER, "--help", "anything; at all"],
    "sh-c": ["sh", "-c", f"python -m {MODULE}"],
    "sh-c-exec": ["/bin/sh", "-c", f"exec python -u -m {MODULE}"],
    "sh-c-surrounding-whitespace": ["sh", "-c", f"\n  exec  python -m {MODULE} \n"],
    "bash-c": ["/bin/bash", "-c", f"python -m {MODULE}"],
    "dash-c": ["dash", "-c", f"python -m {MODULE}"],
    "entrypoint-before-exec": ["/bin/sh", "/app/scripts/start.sh", *SCHEDULER],
    "entrypoint-direct": ["/app/scripts/start.sh", *SCHEDULER],
    "entrypoint-shell": [
        "/bin/sh",
        "/app/scripts/start.sh",
        "sh",
        "-c",
        f"exec python -u -m {MODULE}",
    ],
    "docker-init-entrypoint": [
        "/sbin/docker-init",
        "--",
        "/app/scripts/start.sh",
        *SCHEDULER,
    ],
    "tini-options": ["tini", "-s", "-g", "-w", "-vvv", "-p", "SIGTERM", "-e", "143"]
    + ["--", *SCHEDULER],
    "tini-without-separator": ["/usr/bin/tini", *SCHEDULER],
    "dumb-init-options": ["dumb-init", "--single-child", "-v", "--rewrite", "15:2"]
    + ["-r", "2:15", "--rewrite=1:15", "-c", "--verbose", *SCHEDULER],
    "env-assignments": ["/usr/bin/env", "PYTHONUNBUFFERED=1", "TZ=UTC", *SCHEDULER],
    "env-plain": ["env", *SCHEDULER],
    "four-launchers": [
        "/sbin/docker-init",
        "--",
        "/bin/sh",
        "/app/scripts/start.sh",
        "env",
        "A=1",
        "env",
        *SCHEDULER,
    ],
}


@pytest.mark.parametrize(
    "argv", SCHEDULER_FORMS.values(), ids=list(SCHEDULER_FORMS.keys())
)
def test_supported_scheduler_forms_skip_http(
    argv: list[str], tmp_path: Path, http: MagicMock
) -> None:
    """The documented scheduler forms are recognised from PID 1 alone."""
    _assert_scheduler(_cmdline(argv), tmp_path, http)


API_FORMS = {
    # Compound shell scripts are never interpreted.
    "api-and-scheduler-background": [
        "sh",
        "-c",
        f"uvicorn podex.main:app & python -m {MODULE}",
    ],
    "non-listener-and-scheduler": ["sh", "-c", f"sleep 600 & python -m {MODULE}"],
    "scheduler-background-wait": ["sh", "-c", f"python -u -m {MODULE} & wait"],
    "after-exec-uvicorn": [
        "sh",
        "-c",
        f"exec uvicorn podex.main:app; python -m {MODULE}",
    ],
    "conditional": [
        "sh",
        "-c",
        f"true && python -m {MODULE} || uvicorn podex.main:app",
    ],
    "if-branch": [
        "sh",
        "-c",
        f"if false; then python -m {MODULE}; fi; uvicorn podex.main:app",
    ],
    "command-substitution": [
        "sh",
        "-c",
        f"python -m {MODULE} $(uvicorn podex.main:app)",
    ],
    "backtick-substitution": [
        "sh",
        "-c",
        f"python -m {MODULE} `uvicorn podex.main:app`",
    ],
    "heredoc": [
        "sh",
        "-c",
        f"cat <<EOF\npython -m {MODULE}\nEOF\nuvicorn podex.main:app",
    ],
    "quoted-semicolon": [
        "sh",
        "-c",
        f'echo ";" python -m {MODULE}; exec uvicorn podex.main:app',
    ],
    "newline-separated": [
        "sh",
        "-c",
        f"python -m {MODULE}\nuvicorn podex.main:app",
    ],
    "assignment-prefix": ["sh", "-c", f"TZ=UTC python -m {MODULE}"],
    "exec-only": ["sh", "-c", "exec"],
    "empty-script": ["sh", "-c", "  "],
    "missing-script": ["sh", "-c"],
    "script-positional-arguments": ["sh", "-c", f"python -m {MODULE}", "name"],
    "bash-lc": ["bash", "-lc", f"python -m {MODULE}"],
    "sh-e-c": ["sh", "-e", "-c", f"python -m {MODULE}"],
    "shell-script-file": ["sh", "/opt/run-scheduler.sh"],
    "uv-run": ["uv", "run", "python", "-m", MODULE],
    "poetry-run": ["poetry", "run", "python", "-m", MODULE],
    "env-split-string": ["env", "-S", f"python -m {MODULE}"],
    "env-option": ["env", "-i", *SCHEDULER],
    "env-separator": ["env", "--", *SCHEDULER],
    "env-without-command": ["env", "A=1"],
    "entrypoint-without-command": ["/bin/sh", "/app/scripts/start.sh"],
    "other-script-under-sh": ["/bin/sh", "/app/scripts/other.sh", *SCHEDULER],
    "tini-unknown-option": ["tini", "-x", "--", *SCHEDULER],
    "tini-missing-value": ["tini", "-p"],
    "tini-without-command": ["tini", "--"],
    "dumb-init-tini-option": ["dumb-init", "-s", *SCHEDULER],
    "five-launchers": ["env", "env", "env", "env", "env", *SCHEDULER],
    "combined-flags": ["python", "-um", MODULE],
    "attached-module": ["python", f"-m{MODULE}"],
    "x-option": ["python", "-X", "dev", "-m", MODULE],
    "isolated-flag": ["python", "-I", "-m", MODULE],
    "python-c": ["python", "-c", f"import {MODULE}"],
    "script-path": ["python", "/app/src/podex/scheduler_runner.py"],
    "other-module": ["python", "-m", f"{MODULE}_extra"],
    "missing-module": ["python", "-m"],
    "not-python": ["node", "-m", MODULE],
    "uvicorn": API,
    "host-init": ["/sbin/init", "splash"],
    "pod-pause": ["/pause"],
}


@pytest.mark.parametrize("argv", API_FORMS.values(), ids=list(API_FORMS.keys()))
def test_other_pid1_commands_use_http(
    argv: list[str], tmp_path: Path, http: MagicMock
) -> None:
    """Every other PID 1 command line is probed over HTTP."""
    _assert_api(_cmdline(argv), tmp_path, http)


@pytest.mark.parametrize("character", sorted(";&|<>()$`\n\"'\\#*?[]{}~!"))
def test_shell_metacharacters_use_http(
    character: str, tmp_path: Path, http: MagicMock
) -> None:
    """A metacharacter anywhere in a -c script makes it undecidable."""
    script = f"exec python -u -m {MODULE} x{character}y"
    _assert_api(_cmdline(["sh", "-c", script]), tmp_path, http)


@pytest.mark.parametrize(
    "pid1",
    [b"", b"python -m podex.scheduler_runner", b"\xff\xfe\0-m\0podex\0"],
    ids=["zombie", "rewritten-title", "undecodable"],
)
def test_raw_command_lines_use_http(
    pid1: bytes, tmp_path: Path, http: MagicMock
) -> None:
    """Empty, single-string and undecodable command lines keep the HTTP check."""
    _assert_api(pid1, tmp_path, http)


@pytest.mark.parametrize(
    "commands",
    [
        {1: _cmdline(API), 40: _cmdline(SCHEDULER)},
        {1: _cmdline(API), 41: _cmdline(["pgrep", "-f", MODULE])},
        {1: _cmdline(API), 42: _cmdline(["grep", MODULE])},
        {1: _cmdline(["/sbin/init"]), 812: _cmdline(SCHEDULER)},
        {1: _cmdline(["/pause"]), 7: _cmdline(SCHEDULER)},
        {1: _cmdline(["/sbin/docker-init", "--", *API]), 9: _cmdline(SCHEDULER)},
    ],
    ids=[
        "exec-into-api",
        "pgrep",
        "grep",
        "host-pid-namespace",
        "pod-pid-namespace",
        "reparented-under-init",
    ],
)
def test_other_scheduler_processes_do_not_exempt_api(
    commands: dict[int, bytes], tmp_path: Path, http: MagicMock
) -> None:
    """A visible scheduler that is not PID 1 leaves the HTTP check in force."""
    proc = _process_table(tmp_path, commands)

    assert_that(healthcheck.is_healthy(proc_root=proc, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_many_launchers_are_bounded(tmp_path: Path, http: MagicMock) -> None:
    """Thousands of nested ``env`` launchers neither recurse nor exempt."""
    pid1 = _cmdline(["env"] * 1100 + SCHEDULER)
    started = time.perf_counter()

    _assert_api(pid1, tmp_path, http)
    assert_that(time.perf_counter() - started).is_less_than(1.0)


def test_huge_command_line_is_bounded(tmp_path: Path, http: MagicMock) -> None:
    """A very long command line is not parsed and keeps the HTTP check."""
    pid1 = _cmdline(["env"] + ["-i"] * 100_000 + SCHEDULER)
    started = time.perf_counter()

    _assert_api(pid1, tmp_path, http)
    assert_that(time.perf_counter() - started).is_less_than(1.0)


def test_limits_bound_the_supported_forms(tmp_path: Path, http: MagicMock) -> None:
    """Arguments past the count limit are undecided even under the byte limit."""
    pid1 = _cmdline([*SCHEDULER, *["x"] * healthcheck.MAX_ARGUMENTS])

    assert_that(len(pid1)).is_less_than(healthcheck.MAX_CMDLINE_BYTES)
    _assert_api(pid1, tmp_path, http)


def test_unexpected_parse_error_uses_http(
    tmp_path: Path, http: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any error while deciding the role falls back to the HTTP check."""
    monkeypatch.setattr(
        healthcheck,
        "command_runs_scheduler",
        MagicMock(side_effect=RecursionError("deep")),
    )

    _assert_api(_cmdline(SCHEDULER), tmp_path, http)


def test_unreadable_pid1_uses_http(tmp_path: Path, http: MagicMock) -> None:
    """A PID 1 command line that cannot be read keeps the HTTP check."""
    (tmp_path / "1" / "cmdline").mkdir(parents=True)
    _process_table(tmp_path, {2: _cmdline(SCHEDULER)})

    assert_that(healthcheck.detected_role(tmp_path)).is_equal_to(healthcheck.Role.API)
    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_process_exiting_during_probe_uses_http(
    tmp_path: Path, http: MagicMock
) -> None:
    """A process table entry that vanishes while read keeps the HTTP check."""
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "cmdline").symlink_to(tmp_path / "exited")

    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_missing_process_table_uses_http(tmp_path: Path, http: MagicMock) -> None:
    """Without a readable process table the API probe still runs."""
    assert_that(
        healthcheck.is_healthy(proc_root=tmp_path / "absent", environ={})
    ).is_false()
    assert_that(http.call_count).is_equal_to(1)


@pytest.mark.parametrize(
    "pid1",
    [["sh", "-c", f"python -u -m {MODULE} & wait"], ["/sbin/init"]],
    ids=["compound-shell", "host-pid-namespace"],
)
@pytest.mark.parametrize("value", ["scheduler", " Scheduler "])
def test_scheduler_setting_overrides_detection(
    value: str, pid1: list[str], tmp_path: Path, http: MagicMock
) -> None:
    """The explicit scheduler role covers forms detection does not accept."""
    proc = _process_table(tmp_path, {1: _cmdline(pid1)})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": value})
    ).is_true()
    assert_that(http.called).is_false()


def test_api_setting_overrides_scheduler_detection(
    tmp_path: Path, http: MagicMock
) -> None:
    """The explicit API role probes HTTP even when PID 1 is the scheduler."""
    proc = _process_table(tmp_path, {1: _cmdline(SCHEDULER)})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": "api"})
    ).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_empty_setting_falls_back_to_detection(tmp_path: Path, http: MagicMock) -> None:
    """An empty role setting behaves as if it were unset."""
    proc = _process_table(tmp_path, {1: _cmdline(SCHEDULER)})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": " "})
    ).is_true()
    assert_that(http.called).is_false()


def test_invalid_setting_is_unhealthy(
    tmp_path: Path, http: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown role fails closed and names the accepted values."""
    proc = _process_table(tmp_path, {1: _cmdline(SCHEDULER)})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": "worker"})
    ).is_false()
    assert_that(http.called).is_false()
    assert_that(capsys.readouterr().err).contains(
        "PODEX_PROCESS_ROLE must be one of: api, scheduler"
    )


def test_probe_reads_its_own_environment(
    tmp_path: Path, http: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without an explicit mapping the role setting comes from os.environ."""
    monkeypatch.setenv("PODEX_PROCESS_ROLE", "scheduler")

    assert_that(healthcheck.is_healthy(proc_root=tmp_path)).is_true()
    assert_that(http.called).is_false()


@pytest.mark.parametrize("status", [200, 204, 503])
def test_api_requires_exact_health_success(
    status: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP listener is healthy only when exact /health returns HTTP 200."""
    connection = MagicMock(spec=HTTPConnection)
    connection.getresponse.return_value.status = status
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr(healthcheck, "HTTPConnection", factory)

    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_equal_to(
        status == 200
    )
    assert_that(factory.call_args.args).is_equal_to(("127.0.0.1", 8000))
    assert_that(factory.call_args.kwargs).is_equal_to({"timeout": 2})
    assert_that(connection.request.call_args.args).is_equal_to(("GET", "/health"))
    assert_that(connection.close.call_count).is_equal_to(1)


@pytest.mark.parametrize(
    "error", [OSError("Unavailable"), HTTPException("No response")]
)
def test_api_request_failure_is_unhealthy(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failures and stalled HTTP responses fail the probe quietly."""
    connection = MagicMock(spec=HTTPConnection)
    connection.request.side_effect = error
    monkeypatch.setattr(
        healthcheck,
        "HTTPConnection",
        MagicMock(return_value=connection),
    )

    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_false()
    assert_that(connection.close.call_count).is_equal_to(1)


@pytest.mark.parametrize(("healthy", "status"), [(True, 0), (False, 1)])
def test_main_exit_status(
    healthy: bool, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe exits 0 only when healthy."""
    monkeypatch.setattr(healthcheck, "is_healthy", MagicMock(return_value=healthy))

    assert_that(healthcheck.main()).is_equal_to(status)


@pytest.mark.parametrize(
    "error",
    [RecursionError("deep"), RuntimeError("boom"), OSError("proc")],
    ids=["recursion", "runtime", "os"],
)
def test_main_contains_unexpected_errors(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No exception escapes: exit 1 with one line naming the error type."""
    monkeypatch.setattr(healthcheck, "is_healthy", MagicMock(side_effect=error))

    with pytest.raises(SystemExit) as exited:
        healthcheck.main()

    assert_that(exited.value.code).is_equal_to(1)
    assert_that(capsys.readouterr().err).is_equal_to(
        f"health probe error: {type(error).__name__}\n"
    )
