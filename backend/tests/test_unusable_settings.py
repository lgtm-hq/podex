"""Unparsable credential-bearing values are reported by setting and class only.

Parsing a database URL, a Redis URL or an error-reporting DSN can fail with
text that quotes the value. Each is parsed on its own with its library's
parser inside ``without_setting_text``, which replaces that parser's
input-error classes with an error naming the setting, the validated class and,
for a missing driver the application can need, its module, with the original
error neither chained nor kept as context. Engines and clients are built from
the validated value outside it, so a fault there surfaces unchanged.
"""

import io
import traceback
from pathlib import Path
from secrets import token_hex
from typing import NoReturn

import pytest
import redis
import sqlalchemy
from alembic.command import upgrade
from alembic.config import Config
from assertpy import assert_that
from pydantic import SecretStr
from sqlalchemy.exc import ArgumentError

from podex import database, observability
from podex.config import (
    DatabaseSettings,
    ObservabilitySettings,
    RateLimitSettings,
    Settings,
)
from podex.observability import init_sentry
from podex.services.limiter_factory import build_rate_limiter
from podex.unusable_settings import (
    DATABASE_URL_FAILURES,
    UnusableSettingError,
    checked_database_url,
    without_setting_text,
)

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"
_VALID_DSN = "https://public@sentry.invalid/1"


def _secret() -> str:
    """Return a synthetic secret."""
    return f"setting-secret-{token_hex(8)}"


def _printed(error: BaseException) -> str:
    """Return the error as the interpreter prints it, with its chain."""
    return "".join(traceback.format_exception(error))


def _assert_withheld(error: UnusableSettingError, secret: str) -> None:
    """The error keeps no original error and prints no part of the secret."""
    assert_that(error.__cause__).is_none()
    assert_that(error.__context__).is_none()
    assert_that(_printed(error).lower()).does_not_contain(secret.lower())


def _internal_fault(error: Exception) -> object:
    """Return a stand-in that fails as an internal fault would."""

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        """Raise ``error``."""
        raise error

    return fail


@pytest.mark.parametrize(
    ("template", "load_driver", "failure"),
    [
        # An unescaped "@" and ":" make the parser read the password's rest
        # as the port; its own error quotes it.
        (
            "postgresql+psycopg://podex:pw@part:{secret}@db.invalid/podex",
            True,
            "ValueError",
        ),
        (
            "nodialect://podex:{secret}@db.invalid/podex",
            False,
            "sqlalchemy.exc.NoSuchModuleError",
        ),
        ("not a database URL {secret}", True, "sqlalchemy.exc.ArgumentError"),
        # A driver the application does not use: its name is not printed.
        (
            "postgresql+asyncpg://podex:{secret}@db.invalid/podex",
            True,
            "ModuleNotFoundError",
        ),
    ],
)
def test_unusable_database_url_reports_class_only(
    template: str,
    load_driver: bool,
    failure: str,
) -> None:
    """Parsing an unusable URL names the setting and class, not the URL."""
    secret = _secret()

    with pytest.raises(UnusableSettingError) as caught:
        checked_database_url(
            template.format(secret=secret),
            source="PODEX_DATABASE__URL",
            load_driver=load_driver,
        )

    assert_that(str(caught.value)).starts_with(
        f"PODEX_DATABASE__URL is not usable: {failure}. ",
    )
    _assert_withheld(caught.value, secret)
    assert_that(_printed(caught.value)).does_not_contain("podex:pw")


def test_missing_application_driver_is_named() -> None:
    """A missing driver the application can need is named by its module."""

    def parse() -> None:
        """Fail as importing the PostgreSQL driver would."""
        raise ModuleNotFoundError("No module named 'psycopg'", name="psycopg")

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(
            parse,
            source="PODEX_DATABASE__URL",
            failures=DATABASE_URL_FAILURES,
        )

    assert_that(str(caught.value)).starts_with(
        "PODEX_DATABASE__URL is not usable: ModuleNotFoundError (module psycopg). ",
    )


@pytest.mark.parametrize(
    "module",
    ["asyncpg", "driver:{secret}", "psycopg.{secret}"],
)
def test_other_module_names_are_omitted(module: str) -> None:
    """Any module name outside the driver allowlist is omitted."""
    secret = _secret()
    name = module.format(secret=secret)

    def parse() -> None:
        """Fail to import with an unexpected module name."""
        raise ImportError(secret, name=name)

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(
            parse,
            source="EXAMPLE_SETTING",
            failures=(ImportError,),
        )

    assert_that(str(caught.value)).starts_with(
        "EXAMPLE_SETTING is not usable: ImportError. ",
    )
    _assert_withheld(caught.value, secret)


def test_dynamically_named_error_class_is_withheld() -> None:
    """A class name that is not an identifier is not printed."""
    secret = _secret()
    oddly_named = type(f"bad name {secret}", (ValueError,), {})

    def parse() -> None:
        """Fail with an exception class named by its input."""
        raise oddly_named(secret)

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(parse, source="EXAMPLE_SETTING", failures=(ValueError,))

    assert_that(str(caught.value)).starts_with(
        "EXAMPLE_SETTING is not usable: [withheld]. ",
    )
    _assert_withheld(caught.value, secret)


@pytest.mark.parametrize(
    "failure",
    [ArgumentError, ImportError, ValueError],
    ids=lambda failure: failure.__name__,
)
def test_each_database_input_error_is_withheld(failure: type[Exception]) -> None:
    """Every class the database URL parser raises for bad input is withheld."""
    secret = _secret()

    def parse() -> None:
        """Fail quoting the value, as a parser does."""
        raise failure(f"cannot parse {secret!r}")

    with pytest.raises(UnusableSettingError):
        without_setting_text(
            parse,
            source="PODEX_DATABASE__URL",
            failures=DATABASE_URL_FAILURES,
        )


@pytest.mark.parametrize(
    "error",
    [TypeError("unexpected internal keyword"), KeyError("internal")],
    ids=lambda error: type(error).__name__,
)
def test_other_failures_pass_through(error: Exception) -> None:
    """Failures outside the parser's input errors propagate unchanged."""

    def parse() -> None:
        """Fail as something other than the value would."""
        raise error

    with pytest.raises(type(error)) as caught:
        without_setting_text(
            parse,
            source="PODEX_DATABASE__URL",
            failures=DATABASE_URL_FAILURES,
        )

    assert_that(caught.value).is_same_as(error)


def test_usable_database_url_builds_as_before() -> None:
    """A usable URL builds the engine from the parsed URL."""
    settings = Settings(database=DatabaseSettings(url=SecretStr("sqlite://")))

    engine = database.build_engine(settings)

    assert_that(engine.dialect.name).is_equal_to("sqlite")
    engine.dispose()


@pytest.mark.parametrize(
    "error",
    [TypeError("unexpected internal keyword"), KeyError("internal")],
    ids=lambda error: type(error).__name__,
)
def test_engine_build_fault_with_valid_url_propagates(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault while building the engine from a valid URL is not relabelled."""
    monkeypatch.setattr(database, "create_engine", _internal_fault(error))
    settings = Settings(database=DatabaseSettings(url=SecretStr("sqlite://")))

    with pytest.raises(type(error)) as caught:
        database.build_engine(settings)

    assert_that(caught.value).is_same_as(error)


def test_migration_url_from_alembic_configuration_is_named_as_such() -> None:
    """A URL set in the Alembic configuration is named there, not as the setting."""
    secret = _secret()
    config = Config(str(_ALEMBIC_INI), output_buffer=io.StringIO())
    config.set_main_option(
        "sqlalchemy.url",
        f"postgresql+psycopg://podex:pw@part:{secret}@db.invalid/podex",
    )

    with pytest.raises(UnusableSettingError) as caught:
        upgrade(config, "head", sql=True)

    assert_that(str(caught.value)).starts_with(
        "sqlalchemy.url in the Alembic configuration is not usable: ValueError. ",
    )
    _assert_withheld(caught.value, secret)


def test_migration_engine_fault_with_valid_url_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault while building the migration engine is not relabelled."""
    error = TypeError("unexpected internal keyword")
    monkeypatch.setattr(sqlalchemy, "engine_from_config", _internal_fault(error))
    config = Config(str(_ALEMBIC_INI), output_buffer=io.StringIO())
    config.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_path / 'podex.db'}")

    with pytest.raises(TypeError) as caught:
        upgrade(config, "head")

    assert_that(caught.value).is_same_as(error)


def test_malformed_redis_url_names_setting_and_class_only() -> None:
    """Parsing the shared rate-limit store's URL withholds it."""
    secret = _secret()
    settings = Settings(
        rate_limit=RateLimitSettings(
            redis_url=SecretStr(f"redis://user:{secret}/tail@cache.invalid:6379/0"),
        ),
    )

    with pytest.raises(UnusableSettingError) as caught:
        build_rate_limiter(settings)

    assert_that(str(caught.value)).starts_with(
        "PODEX_RATE_LIMIT__REDIS_URL is not usable: ValueError. ",
    )
    _assert_withheld(caught.value, secret)


@pytest.mark.parametrize(
    "error",
    [TypeError("unexpected internal keyword"), KeyError("internal")],
    ids=lambda error: type(error).__name__,
)
def test_redis_client_fault_with_valid_url_propagates(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault while building the Redis client from a valid URL is not relabelled."""
    monkeypatch.setattr(redis.Redis, "from_url", _internal_fault(error))
    settings = Settings(
        rate_limit=RateLimitSettings(
            redis_url=SecretStr("redis://cache.invalid:6379/0"),
        ),
    )

    with pytest.raises(type(error)) as caught:
        build_rate_limiter(settings)

    assert_that(caught.value).is_same_as(error)


@pytest.mark.parametrize(
    ("template", "failure"),
    [
        ("https://key@127.0.0.1:1/{secret}", "sentry_sdk.utils.BadDsn"),
        ("s{secret}://key@127.0.0.1:1/1", "sentry_sdk.utils.BadDsn"),
        ("https://key:{secret}@127.0.0.1:p{secret}/1", "ValueError"),
    ],
)
def test_malformed_dsn_names_setting_and_class_only(
    template: str,
    failure: str,
) -> None:
    """Parsing a malformed DSN withholds it."""
    secret = _secret()
    settings = Settings(
        observability=ObservabilitySettings(sentry_dsn=template.format(secret=secret)),
    )

    with pytest.raises(UnusableSettingError) as caught:
        init_sentry(settings)

    assert_that(str(caught.value)).starts_with(
        f"PODEX_OBSERVABILITY__SENTRY_DSN is not usable: {failure}. ",
    )
    _assert_withheld(caught.value, secret)


@pytest.mark.parametrize(
    "error",
    [TypeError("unexpected internal keyword"), KeyError("internal")],
    ids=lambda error: type(error).__name__,
)
def test_reporter_fault_with_valid_dsn_propagates(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault while initialising error reporting is not relabelled."""
    monkeypatch.setattr(observability.sentry_sdk, "init", _internal_fault(error))
    settings = Settings(observability=ObservabilitySettings(sentry_dsn=_VALID_DSN))

    with pytest.raises(type(error)) as caught:
        init_sentry(settings)

    assert_that(caught.value).is_same_as(error)
