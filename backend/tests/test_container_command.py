"""Tests for the backend container's default server command."""

import json
from pathlib import Path

from assertpy import assert_that
from uvicorn.main import main as uvicorn_cli

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _default_command() -> list[str]:
    """Return the exec-form ``CMD`` declared by the backend Dockerfile.

    Returns:
        list[str]: The command and its arguments.
    """
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    commands = [line.removeprefix("CMD ") for line in lines if line.startswith("CMD ")]
    assert_that(commands).is_length(1)
    command: list[str] = json.loads(commands[0])
    return command


def test_container_command_disables_uvicorn_access_log() -> None:
    """Uvicorn's access log, which prints query strings, is turned off.

    The application's ``podex.access`` logger records method, path, status
    and duration for every request instead, without the query string.
    """
    command = _default_command()

    assert_that(command[0]).is_equal_to("uvicorn")
    context = uvicorn_cli.make_context("uvicorn", command[1:])
    assert_that(context.params["app"]).is_equal_to("podex.main:app")
    assert_that(context.params["access_log"]).is_false()
