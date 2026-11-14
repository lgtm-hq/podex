"""Exercise container liveness and scheduler command recognition."""

from http.client import HTTPConnection, HTTPException
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from scripts import healthcheck


@pytest.mark.parametrize(
    "commands",
    [
        {1: b"python\0-m\0podex.scheduler_runner\0"},
        {1: b"sh\0-c\0python -m podex.scheduler_runner & wait\0"},
        {1: b"python\0-u\0-m\0podex.scheduler_runner\0"},
        {1: b"/sbin/docker-init\0--\0", 7: b"python\0-m\0podex.scheduler_runner\0"},
    ],
    ids=["module", "shell", "python-options", "init-wrapper"],
)
def test_scheduler_needs_no_http_listener(
    commands: dict[int, bytes],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler command variants stay healthy without invoking the API probe."""
    for process_id, command in commands.items():
        process = tmp_path / str(process_id)
        process.mkdir()
        (process / "cmdline").write_bytes(command)
    factory = MagicMock()
    monkeypatch.setattr(healthcheck, "HTTPConnection", factory)

    assert_that(healthcheck.is_healthy(proc_root=tmp_path)).is_true()
    assert_that(factory.called).is_false()


def test_exited_process_does_not_exempt_api(tmp_path: Path) -> None:
    """Ignore a proc entry that disappears before its command line is read."""
    process = tmp_path / "2"
    process.mkdir()
    (process / "cmdline").symlink_to(tmp_path / "exited")

    assert_that(healthcheck.scheduler_is_running(proc_root=tmp_path)).is_false()


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

    assert_that(healthcheck.is_healthy(proc_root=tmp_path)).is_equal_to(status == 200)
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

    assert_that(healthcheck.is_healthy(proc_root=tmp_path)).is_false()
    assert_that(connection.close.call_count).is_equal_to(1)
