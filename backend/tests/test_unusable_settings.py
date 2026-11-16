"""Unusable URLs and DSNs are reported by setting and error class only.

Parsing a database URL, a Redis URL or an error-reporting DSN can fail with
text that quotes the value. Each is built inside ``without_setting_text``,
which replaces such a failure with an error naming the setting, the original
class and, for a failed import, the missing module, with the original error
neither chained nor kept as context.
"""

import io
import traceback
from pathlib import Path
from secrets import token_hex

import pytest
from alembic.command import upgrade
from alembic.config import Config
from assertpy import assert_that
from pydantic import SecretStr
from sqlalchemy import create_engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from podex.config import ObservabilitySettings, RateLimitSettings, Settings
from podex.observability import init_sentry
from podex.services.limiter_factory import build_rate_limiter
from podex.unusable_settings import UnusableSettingError, without_setting_text

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


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


@pytest.mark.parametrize(
    ("template", "failure"),
    [
        # An unescaped "@" and ":" make the parser read the password's rest
        # as the port; its own error quotes it.
        ("postgresql+psycopg://podex:pw@part:{secret}@db.invalid/podex", "ValueError"),
        ("nodialect://podex:{secret}@db.invalid/podex", "NoSuchModuleError"),
        ("not a database URL {secret}", "ArgumentError"),
        (
            "postgresql+asyncpg://podex:{secret}@db.invalid/podex",
            "ModuleNotFoundError (module asyncpg)",
        ),
    ],
)
def test_unusable_database_url_reports_class_only(template: str, failure: str) -> None:
    """Building an engine from an unusable URL names the class, not the URL."""
    secret = _secret()
    url = template.format(secret=secret)

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(lambda: create_engine(url), source="PODEX_DATABASE__URL")

    assert_that(str(caught.value)).starts_with(
        f"PODEX_DATABASE__URL is not usable: {failure}. ",
    )
    _assert_withheld(caught.value, secret)
    assert_that(_printed(caught.value)).does_not_contain("podex:pw")


def test_dialect_check_reports_class_only() -> None:
    """Parsing a URL and loading its dialect fail the same way."""
    secret = _secret()

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(
            lambda: make_url(
                f"nodialect://podex:{secret}@db.invalid/podex"
            ).get_dialect(),
            source="PODEX_DATABASE__URL",
        )

    assert_that(str(caught.value)).contains("is not usable: NoSuchModuleError.")
    _assert_withheld(caught.value, secret)


@pytest.mark.parametrize(
    "failure",
    [SQLAlchemyError, ImportError, KeyError, LookupError, TypeError, ValueError],
    ids=lambda failure: failure.__name__,
)
def test_each_caught_failure_is_withheld(failure: type[Exception]) -> None:
    """Every failure kind a value's parser raises is reported by class only."""
    secret = _secret()

    def build() -> None:
        """Fail quoting the value, as a parser does."""
        raise failure(f"cannot parse {secret!r}")

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(build, source="EXAMPLE_SETTING")

    assert_that(str(caught.value)).starts_with(
        f"EXAMPLE_SETTING is not usable: {failure.__name__}. ",
    )
    _assert_withheld(caught.value, secret)


def test_import_failure_names_only_a_module_name() -> None:
    """A failed import's name is kept only when it has a module name's form."""
    secret = _secret()

    def build() -> None:
        """Fail to import with a name that is not a module name."""
        raise ImportError(secret, name=f"driver:{secret}")

    with pytest.raises(UnusableSettingError) as caught:
        without_setting_text(build, source="EXAMPLE_SETTING")

    assert_that(str(caught.value)).starts_with(
        "EXAMPLE_SETTING is not usable: ImportError. ",
    )
    _assert_withheld(caught.value, secret)


def test_usable_value_builds_as_before() -> None:
    """A usable value returns what the build returns."""
    engine = without_setting_text(
        lambda: create_engine("sqlite://"),
        source="PODEX_DATABASE__URL",
    )

    assert_that(engine.dialect.name).is_equal_to("sqlite")
    engine.dispose()


def test_other_failures_pass_through() -> None:
    """Failures that are not parsing failures propagate unchanged."""
    error = RuntimeError("not a parsing failure")

    def build() -> None:
        """Fail as something other than the value would."""
        raise error

    with pytest.raises(RuntimeError) as caught:
        without_setting_text(build, source="EXAMPLE_SETTING")

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


def test_malformed_redis_url_names_setting_and_class_only() -> None:
    """Building the shared rate-limit store's client withholds its URL."""
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
    ("template", "failure"),
    [
        ("https://key@127.0.0.1:1/{secret}", "BadDsn"),
        ("s{secret}://key@127.0.0.1:1/1", "BadDsn"),
        ("https://key:{secret}@127.0.0.1:p{secret}/1", "ValueError"),
    ],
)
def test_malformed_dsn_names_setting_and_class_only(
    template: str,
    failure: str,
) -> None:
    """Initialising error reporting with a malformed DSN withholds the DSN."""
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
