"""Tests for the command that writes the OpenAPI schema artifact."""

import json
from pathlib import Path

import pytest
from assertpy import assert_that

from podex import openapi
from podex.openapi import main, render_openapi_json


def test_main_writes_schema_to_the_given_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With a path argument the schema is written there and nothing is printed."""
    target = tmp_path / "openapi.json"
    target.write_text("stale contents", encoding="utf-8")

    exit_code = main([str(target)])

    assert_that(exit_code).is_equal_to(0)
    assert_that(target.read_bytes()).is_equal_to(
        render_openapi_json().encode("utf-8"),
    )
    assert_that(json.loads(target.read_text(encoding="utf-8"))).contains_key(
        "openapi",
        "paths",
    )
    assert_that(capsys.readouterr().out).is_empty()


def test_main_without_arguments_writes_schema_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no argument the newline-terminated schema goes to stdout."""
    exit_code = main([])

    assert_that(exit_code).is_equal_to(0)
    printed = capsys.readouterr().out
    assert_that(printed).is_equal_to(render_openapi_json())
    assert_that(printed).ends_with("}\n")


def test_main_reads_process_arguments_when_none_are_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Called as the module entry point it takes the path from ``sys.argv``."""
    target = tmp_path / "from-argv.json"
    monkeypatch.setattr(openapi.sys, "argv", ["podex.openapi", str(target)])

    exit_code = main()

    assert_that(exit_code).is_equal_to(0)
    assert_that(target.read_text(encoding="utf-8")).is_equal_to(
        render_openapi_json(),
    )
    assert_that(capsys.readouterr().out).is_empty()
