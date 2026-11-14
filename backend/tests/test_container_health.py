"""Exercise the container probe's role decision and API liveness check."""

from http.client import HTTPConnection, HTTPException
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from scripts import healthcheck

SCHEDULER = b"python\0-m\0podex.scheduler_runner\0"
API = b"\0".join([b"uvicorn", b"podex.main:app", b"--host", b"0.0.0.0", b""])


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


@pytest.mark.parametrize(
    "pid1",
    [
        SCHEDULER,
        b"/app/.venv/bin/python3.15\0-m\0podex.scheduler_runner\0",
        b"python\0-u\0-m\0podex.scheduler_runner\0",
        b"python\0-um\0podex.scheduler_runner\0",
        b"python\0-mpodex.scheduler_runner\0",
        b"python\0-Wignore\0-W\0error\0-X\0dev\0-m\0podex.scheduler_runner\0",
        b"python\0--check-hash-based-pycs\0never\0-m\0podex.scheduler_runner\0",
        b"sh\0-c\0python -m podex.scheduler_runner & wait\0",
        b"/bin/bash\0-ec\0cd /app && exec python -u -m podex.scheduler_runner\0",
        b"sh\0-c\0TZ=UTC python -m podex.scheduler_runner 2>&1; exit $?\0",
        b"/sbin/docker-init\0--\0/app/scripts/start.sh\0" + SCHEDULER,
        b"tini\0-s\0-p\0SIGTERM\0--\0" + SCHEDULER,
        b"dumb-init\0--rewrite\0" + b"15:2\0" + SCHEDULER,
        b"/bin/sh\0/app/scripts/start.sh\0" + SCHEDULER,
        b"/app/scripts/start.sh\0sh\0-c\0python -m podex.scheduler_runner\0",
        b"env\0-u\0HOME\0PYTHONUNBUFFERED=1\0" + SCHEDULER,
    ],
    ids=[
        "exec-form",
        "venv-interpreter",
        "python-u-m",
        "combined-flags",
        "attached-module",
        "value-options",
        "long-option",
        "sh-c",
        "bash-exec",
        "sh-c-assignment-redirect",
        "init-wrapper-entrypoint",
        "tini-options",
        "dumb-init-options",
        "entrypoint-before-exec",
        "entrypoint-shell",
        "env-wrapper",
    ],
)
def test_scheduler_pid1_needs_no_http_listener(
    pid1: bytes, tmp_path: Path, http: MagicMock
) -> None:
    """Scheduler start forms are recognized from PID 1 alone."""
    proc = _process_table(tmp_path, {1: pid1})

    assert_that(healthcheck.detected_role(proc)).is_equal_to(healthcheck.Role.SCHEDULER)
    assert_that(healthcheck.is_healthy(proc_root=proc, environ={})).is_true()
    assert_that(http.called).is_false()


@pytest.mark.parametrize(
    "commands",
    [
        {1: API, 40: SCHEDULER},
        {1: API, 41: b"pgrep\0-f\0podex.scheduler_runner\0"},
        {1: API, 42: b"grep\0podex.scheduler_runner\0"},
        {1: b"/sbin/init\0splash\0", 812: SCHEDULER},
        {1: b"/pause\0", 7: SCHEDULER},
        {1: b"/sbin/docker-init\0--\0/app/scripts/start.sh\0" + API, 9: SCHEDULER},
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


@pytest.mark.parametrize(
    "pid1",
    [
        API,
        b"",
        b"python\0-c\0import podex.scheduler_runner\0",
        b"python\0/app/podex/scheduler_runner.py\0",
        b"python\0-m\0podex.scheduler_runner_extra\0",
        b"python\0-m\0",
        b"python\0-X\0",
        b"python\0-\0-m\0podex.scheduler_runner\0",
        b"python\0--check-hash-based-pycs\0",
        b"node\0-m\0podex.scheduler_runner\0",
        b"sh\0/opt/run.sh\0podex.scheduler_runner\0",
        b"sh\0-c\0",
        b"sh\0-e\0",
        b"sh\0-c\0echo podex.scheduler_runner\0",
        b"sh\0-c\0python -m 'podex.scheduler_runner\0",
        b"tini\0--\0",
        b"env\0",
        b"\xff\xfe\0-m\0podex.scheduler_runner\0",
        b"python -m podex.scheduler_runner",
    ],
    ids=[
        "uvicorn",
        "zombie",
        "python-c",
        "script-path",
        "other-module",
        "missing-module",
        "missing-option-value",
        "stdin-script",
        "missing-long-option-value",
        "not-python",
        "shell-script-file",
        "empty-shell-script",
        "shell-without-command",
        "module-name-as-text",
        "unbalanced-quote",
        "init-without-command",
        "env-without-command",
        "undecodable",
        "rewritten-title",
    ],
)
def test_other_pid1_commands_use_api_check(
    pid1: bytes, tmp_path: Path, http: MagicMock
) -> None:
    """Anything PID 1 runs other than the scheduler is probed over HTTP."""
    proc = _process_table(tmp_path, {1: pid1})

    assert_that(healthcheck.detected_role(proc)).is_equal_to(healthcheck.Role.API)
    assert_that(healthcheck.is_healthy(proc_root=proc, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_unreadable_pid1_uses_api_check(tmp_path: Path, http: MagicMock) -> None:
    """A PID 1 command line that cannot be read keeps the HTTP check."""
    (tmp_path / "1" / "cmdline").mkdir(parents=True)
    _process_table(tmp_path, {2: SCHEDULER})

    assert_that(healthcheck.detected_role(tmp_path)).is_equal_to(healthcheck.Role.API)
    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_process_exiting_during_probe_uses_api_check(
    tmp_path: Path, http: MagicMock
) -> None:
    """A process table entry that vanishes while read keeps the HTTP check."""
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "cmdline").symlink_to(tmp_path / "exited")
    (tmp_path / "3").mkdir()
    (tmp_path / "3" / "cmdline").symlink_to(tmp_path / "also-exited")

    assert_that(healthcheck.is_healthy(proc_root=tmp_path, environ={})).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_missing_process_table_uses_api_check(tmp_path: Path, http: MagicMock) -> None:
    """Without a readable process table the API probe still runs."""
    assert_that(
        healthcheck.is_healthy(proc_root=tmp_path / "absent", environ={})
    ).is_false()
    assert_that(http.call_count).is_equal_to(1)


@pytest.mark.parametrize("value", ["scheduler", " Scheduler "])
def test_scheduler_setting_overrides_detection(
    value: str, tmp_path: Path, http: MagicMock
) -> None:
    """The explicit scheduler role needs no HTTP listener whatever PID 1 is."""
    proc = _process_table(tmp_path, {1: b"/sbin/init\0"})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": value})
    ).is_true()
    assert_that(http.called).is_false()


def test_api_setting_overrides_scheduler_detection(
    tmp_path: Path, http: MagicMock
) -> None:
    """The explicit API role probes HTTP even when PID 1 is the scheduler."""
    proc = _process_table(tmp_path, {1: SCHEDULER})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": "api"})
    ).is_false()
    assert_that(http.call_count).is_equal_to(1)


def test_empty_setting_falls_back_to_detection(tmp_path: Path, http: MagicMock) -> None:
    """An empty role setting behaves as if it were unset."""
    proc = _process_table(tmp_path, {1: SCHEDULER})

    assert_that(
        healthcheck.is_healthy(proc_root=proc, environ={"PODEX_PROCESS_ROLE": " "})
    ).is_true()
    assert_that(http.called).is_false()


def test_invalid_setting_is_unhealthy(
    tmp_path: Path, http: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown role fails closed and names the accepted values."""
    proc = _process_table(tmp_path, {1: SCHEDULER})

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


@pytest.mark.parametrize(
    ("outcome", "status"),
    [(True, 0), (False, 1), (OSError("proc"), 1), (ValueError("bad"), 1)],
    ids=["healthy", "unhealthy", "os-error", "value-error"],
)
def test_main_exit_status(
    outcome: bool | Exception, status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe exits 0 only when healthy; internal errors exit 1."""
    probe = MagicMock(
        side_effect=outcome if isinstance(outcome, Exception) else None,
        return_value=outcome,
    )
    monkeypatch.setattr(healthcheck, "is_healthy", probe)

    assert_that(healthcheck.main()).is_equal_to(status)
