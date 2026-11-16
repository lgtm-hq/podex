"""Every entrypoint starts without printing a configured credential.

Each test starts one entrypoint in a new interpreter (the API's import, the
ASGI server command, the scheduler module command, the OpenAPI generator, the
migration command offline and online) with a synthetic secret in its
environment, from a temporary directory (so no local ``.env`` file is read),
and searches everything the process wrote to its standard streams:

- an invalid value in a credential-bearing setting fails with the field path
  and error type, not the rejected input;
- an unusable database URL, a malformed Redis URL or a malformed
  error-reporting DSN fails with the setting's name and the error class, not
  the value;
- with the settings library's debug switch on and DEBUG logging configured
  before the entrypoint loads, valid settings start as before, and the
  library's record of the values it read is withheld.
"""

import contextlib
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex

import pytest
from assertpy import assert_that

from tests import process_support
from tests.process_support import CommandResult, run_command

_BACKEND = Path(__file__).resolve().parent.parent
_OFFLINE_POSTGRES = "postgresql+psycopg://podex@db.invalid:5432/podex"
_DEBUG_LOGGING = "import logging; logging.basicConfig(level=logging.DEBUG); "
_WITHHELD_SETTINGS_RECORD = "pydantic_settings:[record withheld: unrecognised form"
# The migration command configures logging from alembic.ini, which resets the
# root logger's level; a handler on the settings library's own logger still
# prints its records, so the boundary has something to withhold.
_SETTINGS_LOGGER_HANDLER = (
    "import sys; handler = logging.StreamHandler(sys.stderr); "
    "handler.setFormatter(logging.Formatter('%(levelname)s:%(name)s:%(message)s')); "
    "settings_logger = logging.getLogger('pydantic_settings'); "
    "settings_logger.setLevel(logging.DEBUG); settings_logger.addHandler(handler); "
)


@dataclass(frozen=True, slots=True)
class Entrypoint:
    """How to start one entrypoint.

    Attributes:
        name: Test id.
        command: Its documented command, after ``python``; ``{out}`` is a
            writable file path and ``{ini}`` the migration configuration.
        debug_code: Python code that configures DEBUG logging, then loads the
            entrypoint and returns; the same placeholders apply.
        valid_output: Text the debug start prints when it succeeds.
        migrations: Whether it needs the migration configuration and the
            offline PostgreSQL URL.
    """

    name: str
    command: tuple[str, ...]
    debug_code: str = ""
    valid_output: tuple[str, ...] = (_WITHHELD_SETTINGS_RECORD,)
    migrations: bool = False


API = Entrypoint(
    name="api",
    command=("-c", "import podex.main"),
    debug_code="import podex.main",
)
ASGI_SERVER = Entrypoint(
    name="uvicorn",
    command=("-m", "uvicorn", "podex.main:app", "--port", "0", "--no-access-log"),
)
SCHEDULER = Entrypoint(
    name="scheduler",
    command=("-m", "podex.scheduler_runner"),
    debug_code="import podex.scheduler_runner",
)
OPENAPI = Entrypoint(
    name="openapi",
    command=("-m", "podex.openapi", "{out}"),
    debug_code="from podex.openapi import main; main([{out!r}])",
)
MIGRATIONS = Entrypoint(
    name="migrations",
    command=("-m", "alembic", "-c", "{ini}", "upgrade", "head", "--sql"),
    debug_code=(
        _SETTINGS_LOGGER_HANDLER + "from alembic.config import main; "
        "main(argv=['-c', {ini!r}, 'upgrade', 'head', '--sql'])"
    ),
    valid_output=("CREATE TABLE", _WITHHELD_SETTINGS_RECORD),
    migrations=True,
)
MIGRATIONS_ONLINE = Entrypoint(
    name="migrations-online",
    command=("-m", "alembic", "-c", "{ini}", "upgrade", "head"),
    migrations=True,
)
ENTRYPOINTS = (API, SCHEDULER, OPENAPI, MIGRATIONS)


def _alembic_ini(directory: Path, url: str = "") -> Path:
    """Write the migration configuration with its paths made absolute.

    Alembic resolves ``script_location`` and ``prepend_sys_path`` against the
    working directory; absolute paths let the command run from ``directory``.
    ``url``, when given, is written as ``sqlalchemy.url`` as it stands, so the
    configuration parser interpolates it.
    """
    lines = (_BACKEND / "alembic.ini").read_text(encoding="utf-8").splitlines()
    replaced = {
        "script_location = alembic": f"script_location = {_BACKEND / 'alembic'}",
        "prepend_sys_path = .": f"prepend_sys_path = {_BACKEND}",
        "sqlalchemy.url =": f"sqlalchemy.url = {url}".rstrip(),
    }
    assert_that(lines).contains(*replaced)
    rewritten = [replaced.get(line, line) for line in lines]
    path = directory / "alembic.ini"
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return path


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
    configured_url: str = "",
) -> CommandResult:
    """Start ``entrypoint`` from ``tmp_path`` with ``settings``; return its output.

    ``configured_url`` is the migration configuration's ``sqlalchemy.url``.
    """
    places = {
        "out": str(tmp_path / "openapi.json"),
        "ini": (
            str(_alembic_ini(tmp_path, configured_url)) if entrypoint.migrations else ""
        ),
    }
    if debug:
        arguments: tuple[str, ...] = (
            "-c",
            _DEBUG_LOGGING + entrypoint.debug_code.format(**places),
        )
    else:
        arguments = tuple(part.format(**places) for part in entrypoint.command)
    return run_command(
        (sys.executable, *arguments),
        env=_environment(entrypoint, settings),
        cwd=tmp_path,
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
        failure="sqlalchemy.exc.NoSuchModuleError",
    ),
)


@pytest.mark.parametrize("unusable", UNUSABLE_URLS, ids=lambda case: case.name)
@pytest.mark.parametrize(
    "entrypoint",
    [*ENTRYPOINTS, MIGRATIONS_ONLINE],
    ids=lambda entry: entry.name,
)
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
        f"UnusableSettingError: PODEX_DATABASE__URL is not usable: {unusable.failure}.",
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
    assert_that(result.stdout + result.stderr).contains(*entrypoint.valid_output)
    assert_that(result.stdout + result.stderr).does_not_contain(secret)


@dataclass(frozen=True, slots=True)
class MalformedValue:
    """A malformed URL or DSN whose parser quoted part of it.

    Attributes:
        name: Test id.
        variable: The environment variable set.
        template: Its value; ``{secret}`` is the synthetic secret.
        failure: The error class the start-up failure must name.
    """

    name: str
    variable: str
    template: str
    failure: str


_REDIS = "PODEX_RATE_LIMIT__REDIS_URL"
_DSN = "PODEX_OBSERVABILITY__SENTRY_DSN"
REDIS_URLS = (
    # A "/" in the password ends the authority early: the rest is the port.
    MalformedValue(
        name="password-read-as-port",
        variable=_REDIS,
        template="redis://user:{secret}/tail@cache.invalid:6379/0",
        failure="ValueError",
    ),
    MalformedValue(
        name="query-option",
        variable=_REDIS,
        template="redis://user:pw@cache.invalid:6379/0?socket_timeout={secret}",
        failure="ValueError",
    ),
    MalformedValue(
        name="port-text",
        variable=_REDIS,
        template="redis://user:pw@cache.invalid:port{secret}/0",
        failure="ValueError",
    ),
)
DSNS = (
    MalformedValue(
        name="invalid-project",
        variable=_DSN,
        template="https://key@127.0.0.1:1/{secret}",
        failure="sentry_sdk.utils.BadDsn",
    ),
    MalformedValue(
        name="unsupported-scheme",
        variable=_DSN,
        template="s{secret}://key@127.0.0.1:1/1",
        failure="sentry_sdk.utils.BadDsn",
    ),
    MalformedValue(
        name="port-text",
        variable=_DSN,
        template="https://key:{secret}@127.0.0.1:p{secret}/1",
        failure="ValueError",
    ),
)
# Every value through the API's import; the first of each through the other
# entrypoints that build the same client.
MALFORMED_STARTS = [
    *[(API, value) for value in (*REDIS_URLS, *DSNS)],
    (ASGI_SERVER, REDIS_URLS[0]),
    (OPENAPI, REDIS_URLS[0]),
    (ASGI_SERVER, DSNS[0]),
    (SCHEDULER, DSNS[0]),
]


@pytest.mark.parametrize(
    ("entrypoint", "malformed"),
    MALFORMED_STARTS,
    ids=[
        f"{entry.name}-{value.variable}-{value.name}"
        for entry, value in MALFORMED_STARTS
    ],
)
def test_malformed_client_url_reports_setting_and_class_only(
    entrypoint: Entrypoint,
    malformed: MalformedValue,
    tmp_path: Path,
) -> None:
    """Building the Redis or error-reporting client names the setting only."""
    secret = _secret()

    result = _start(
        entrypoint,
        tmp_path,
        {
            "PODEX_DATABASE__URL": f"sqlite:///{tmp_path / 'podex.db'}",
            malformed.variable: malformed.template.format(secret=secret),
        },
    )

    printed = (result.stdout + result.stderr).lower()
    assert_that(result.returncode).described_as(result.describe()).is_not_zero()
    assert_that(result.stderr).contains(
        f"UnusableSettingError: {malformed.variable} is not usable: "
        f"{malformed.failure}.",
    )
    # The DSN parser lower-cases the scheme it quotes.
    assert_that(printed).does_not_contain(secret.lower())


@pytest.mark.parametrize(
    ("template", "failure"),
    [
        # A percent-encoded password: a lone "%" is invalid interpolation.
        (
            "postgresql+psycopg://podex:{secret}%40{secret}@db.invalid/podex",
            "configparser.InterpolationSyntaxError",
        ),
        # An interpolation reference naming an option that does not exist.
        (
            "postgresql+psycopg://podex:%({secret})s@db.invalid/podex",
            "configparser.InterpolationMissingOptionError",
        ),
    ],
    ids=["lone-percent", "missing-reference"],
)
@pytest.mark.parametrize(
    "entrypoint",
    [MIGRATIONS, MIGRATIONS_ONLINE],
    ids=lambda entry: entry.name,
)
def test_configured_url_interpolation_reports_source_and_class_only(
    entrypoint: Entrypoint,
    template: str,
    failure: str,
    tmp_path: Path,
) -> None:
    """A URL in the migration configuration that cannot be read is withheld."""
    secret = _secret()

    result = _start(
        entrypoint,
        tmp_path,
        {},
        configured_url=template.format(secret=secret),
    )

    assert_that(result.returncode).described_as(result.describe()).is_not_zero()
    assert_that(result.stderr).contains(
        "UnusableSettingError: sqlalchemy.url in the Alembic configuration is "
        f"not usable: {failure}.",
    )
    assert_that(result.stdout + result.stderr).does_not_contain(secret)


def test_timed_out_command_is_killed_and_reported(tmp_path: Path) -> None:
    """A command that never finishes is killed, waited for and reported."""
    never_finishes = (
        "import sys, threading; "
        "print('still waiting', file=sys.stderr, flush=True); "
        "print('child started', flush=True); "
        "threading.Event().wait()"
    )

    with pytest.raises(AssertionError) as caught:
        run_command(
            (sys.executable, "-c", never_finishes),
            env=dict(os.environ),
            cwd=tmp_path,
            timeout=0.5,
            ready="child started",
        )

    assert_that(str(caught.value)).contains("command timed out after 0.5s")
    assert_that(str(caught.value)).contains("--- stdout ---\nchild started")
    assert_that(str(caught.value)).contains("--- stderr ---\nstill waiting")


def test_inherited_pipes_do_not_hold_the_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A process left running with the command's pipes cannot hold the result.

    It starts its own session, so the command's cleanup does not reach it, and
    this test kills it, whatever happens. Signalling it succeeds only because
    it was still running, holding the pipes, when the result came back.
    """
    monkeypatch.setattr(process_support, "DRAIN_TIMEOUT_SECONDS", 0.5)
    pid_file = tmp_path / "left-running.pid"
    leaves_a_process = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], start_new_session=True); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
        "print('started', flush=True)"
    )

    try:
        result = run_command(
            (sys.executable, "-c", leaves_a_process, str(pid_file)),
            env=dict(os.environ),
            cwd=tmp_path,
        )
        os.kill(int(pid_file.read_text()), 0)
    finally:
        if pid_file.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)

    assert_that(result.returncode).described_as(result.describe()).is_zero()
    assert_that(result.stdout).is_equal_to("started\n")
