"""Every entrypoint starts without printing a configured credential.

Each test starts one entrypoint in a new interpreter (the API's import, the
scheduler module command, the OpenAPI generator, the migration command) with
a synthetic secret in its environment and searches everything the process
wrote to its standard streams:

- an invalid value in a credential-bearing setting fails with the field path
  and error type, not the rejected input;
- an unusable database URL with a password fails with the error class, not
  the URL;
- with the settings library's debug switch on and DEBUG logging configured
  before the entrypoint loads, valid settings start as before, and the
  library's record of the values it read is withheld.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

import pytest
from assertpy import assert_that

from tests.process_support import CommandResult, run_command

_BACKEND = Path(__file__).resolve().parent.parent
_OFFLINE_POSTGRES = "postgresql+psycopg://podex@db.invalid:5432/podex"
_DEBUG_LOGGING = "import logging; logging.basicConfig(level=logging.DEBUG); "


@dataclass(frozen=True, slots=True)
class Entrypoint:
    """How to start one entrypoint.

    Attributes:
        name: Test id.
        command: Its documented command, after ``python``.
        debug_code: Python code that configures DEBUG logging, then loads the
            entrypoint and returns; ``{out}`` is a writable file path.
        valid_output: Text the debug start prints when it succeeds.
        migrations: Whether it runs from the backend directory against the
            offline PostgreSQL URL (the migration environment's needs).
    """

    name: str
    command: tuple[str, ...]
    debug_code: str
    valid_output: str
    migrations: bool = False


_WITHHELD_SETTINGS_RECORD = "pydantic_settings:[record withheld: unrecognised form"

ENTRYPOINTS = (
    Entrypoint(
        name="api",
        command=("-c", "import podex.main"),
        debug_code="import podex.main",
        valid_output=_WITHHELD_SETTINGS_RECORD,
    ),
    Entrypoint(
        name="scheduler",
        command=("-m", "podex.scheduler_runner"),
        debug_code="import podex.scheduler_runner",
        valid_output=_WITHHELD_SETTINGS_RECORD,
    ),
    Entrypoint(
        name="openapi",
        command=("-m", "podex.openapi", "{out}"),
        debug_code="from podex.openapi import main; main([{out!r}])",
        valid_output=_WITHHELD_SETTINGS_RECORD,
    ),
    Entrypoint(
        name="migrations",
        command=("-m", "alembic", "upgrade", "head", "--sql"),
        debug_code=(
            "from alembic.config import main; "
            "main(argv=['upgrade', 'head', '--sql'])"
        ),
        valid_output="CREATE TABLE",
        migrations=True,
    ),
)


def _environment(entrypoint: Entrypoint, settings: dict[str, str]) -> dict[str, str]:
    """Return this process's environment without its settings, plus ``settings``."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("PODEX_") and name != "PYDANTIC_SETTINGS_DEBUG"
    }
    if entrypoint.migrations:
        environment["PODEX_DATABASE__URL"] = _OFFLINE_POSTGRES
    environment.update(settings)
    return environment


def _start(
    entrypoint: Entrypoint,
    tmp_path: Path,
    settings: dict[str, str],
    *,
    debug: bool = False,
) -> CommandResult:
    """Start ``entrypoint`` with ``settings`` and return what it printed."""
    out = str(tmp_path / "openapi.json")
    if debug:
        arguments: tuple[str, ...] = (
            "-c",
            _DEBUG_LOGGING + entrypoint.debug_code.format(out=out),
        )
    else:
        arguments = tuple(part.format(out=out) for part in entrypoint.command)
    return run_command(
        (sys.executable, *arguments),
        env=_environment(entrypoint, settings),
        cwd=_BACKEND if entrypoint.migrations else tmp_path,
    )


def _secret() -> str:
    """Return a synthetic secret."""
    return f"startup-secret-{token_hex(8)}"


@dataclass(frozen=True, slots=True)
class InvalidSetting:
    """An invalid value in a credential-bearing setting.

    Attributes:
        name: Test id.
        variable: The environment variable set.
        template: Its value; ``{secret}`` is the synthetic secret.
        path: The field path the error must name.
        error_type: The error type it must name.
    """

    name: str
    variable: str
    template: str
    path: str
    error_type: str


INVALID_SETTINGS = (
    InvalidSetting(
        name="database-url-as-section",
        variable="PODEX_DATABASE",
        template='"postgresql+psycopg://podex:{secret}@db.invalid/podex"',
        path="database",
        error_type="model_type",
    ),
    InvalidSetting(
        name="api-key-not-a-string",
        variable="PODEX_ENRICHMENT__TMDB",
        template='{{"api_key": ["{secret}"]}}',
        path="enrichment.tmdb.api_key",
        error_type="string_type",
    ),
)


@pytest.mark.parametrize("invalid", INVALID_SETTINGS, ids=lambda case: case.name)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS, ids=lambda entry: entry.name)
def test_invalid_credential_setting_reports_path_and_type_only(
    entrypoint: Entrypoint,
    invalid: InvalidSetting,
    tmp_path: Path,
) -> None:
    """Start-up fails naming the field and error type, never the input."""
    secret = _secret()

    result = _start(
        entrypoint,
        tmp_path,
        {invalid.variable: invalid.template.format(secret=secret)},
    )

    assert_that(result.returncode).described_as(result.describe()).is_not_zero()
    assert_that(result.stderr).contains("ValidationError: 1 validation error")
    assert_that(result.stderr).contains(f"\n{invalid.path}\n")
    assert_that(result.stderr).contains(f"[type={invalid.error_type}]")
    assert_that(result.stdout + result.stderr).does_not_contain(secret)


@dataclass(frozen=True, slots=True)
class UnusableUrl:
    """An unusable database URL carrying a password.

    Attributes:
        name: Test id.
        template: The URL; ``{secret}`` is part of the password.
        userinfo: The URL's user name and the password's part before the
            secret.
        failure: The error class the start-up failure must name.
    """

    name: str
    template: str
    userinfo: str
    failure: str


UNUSABLE_URLS = (
    # An unescaped "@" and ":" in the password: the rest of it is parsed as
    # the port, and the parser's error quoted it.
    UnusableUrl(
        name="password-read-as-port",
        template="postgresql+psycopg://podex:pw@part:{secret}@db.invalid/podex",
        userinfo="podex:pw@part",
        failure="ValueError",
    ),
    UnusableUrl(
        name="unknown-dialect",
        template="nodialect://podex:pw{secret}@db.invalid/podex",
        userinfo="podex:pw",
        failure="NoSuchModuleError",
    ),
)


@pytest.mark.parametrize("unusable", UNUSABLE_URLS, ids=lambda case: case.name)
@pytest.mark.parametrize("entrypoint", ENTRYPOINTS, ids=lambda entry: entry.name)
def test_unusable_database_url_reports_error_class_only(
    entrypoint: Entrypoint,
    unusable: UnusableUrl,
    tmp_path: Path,
) -> None:
    """Start-up fails naming the error class, never any part of the password."""
    secret = _secret()

    result = _start(
        entrypoint,
        tmp_path,
        {"PODEX_DATABASE__URL": unusable.template.format(secret=secret)},
    )

    printed = result.stdout + result.stderr
    assert_that(result.returncode).described_as(result.describe()).is_not_zero()
    assert_that(result.stderr).contains(
        "DatabaseUrlError: The database URL (PODEX_DATABASE__URL) is not usable: "
        f"{unusable.failure}.",
    )
    assert_that(printed).does_not_contain(secret)
    assert_that(printed).does_not_contain(unusable.userinfo)


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS, ids=lambda entry: entry.name)
def test_settings_debug_switch_prints_no_credential(
    entrypoint: Entrypoint,
    tmp_path: Path,
) -> None:
    """Valid settings start as before; the settings library's record is withheld."""
    secret = _secret()
    settings = {
        "PYDANTIC_SETTINGS_DEBUG": "1",
        "PODEX_ENRICHMENT__TMDB__API_KEY": secret,
        "PODEX_OPS_API_KEY": secret,
    }
    if not entrypoint.migrations:
        settings["PODEX_DATABASE__URL"] = f"sqlite:///{tmp_path / 'podex.db'}"

    result = _start(entrypoint, tmp_path, settings, debug=True)

    assert_that(result.returncode).described_as(result.describe()).is_zero()
    assert_that(result.stdout + result.stderr).contains(entrypoint.valid_output)
    assert_that(result.stdout + result.stderr).does_not_contain(secret)


def test_timed_out_command_is_killed_and_reported(tmp_path: Path) -> None:
    """A command that never finishes is killed, waited for and reported."""
    never_finishes = (
        "import sys, threading; print('child started', flush=True); "
        "print('still waiting', file=sys.stderr, flush=True); "
        "threading.Event().wait()"
    )

    with pytest.raises(AssertionError) as caught:
        run_command(
            (sys.executable, "-c", never_finishes),
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=2,
        )

    assert_that(str(caught.value)).contains("command timed out after 2s")
    assert_that(str(caught.value)).contains("--- stdout ---\nchild started")
    assert_that(str(caught.value)).contains("--- stderr ---\nstill waiting")
