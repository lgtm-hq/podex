"""Settings and database URL errors carry no configured value.

A failed start-up is printed by the interpreter, not by logging, so the
exception text itself must be safe: a settings validation error names the
field path and error type only, and an unusable database URL is reported by
its error class only. Settings resolution installs the log boundary before it
reads the environment.
"""

import traceback
from collections.abc import Iterator
from secrets import token_hex

import pytest
from assertpy import assert_that
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import create_engine, make_url

from podex import config
from podex.config import Settings, SettingsSection, get_settings
from podex.database_url import DatabaseUrlError, without_url_text


def _model_fields(
    model: type[BaseModel], prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], object]]:
    """Yield every field path under ``model`` with its annotation, depth first."""
    for name, field in model.model_fields.items():
        path = (*prefix, name)
        yield path, field.annotation
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from _model_fields(annotation, path)


SECRET_PATHS = [
    ".".join(path)
    for path, annotation in _model_fields(Settings)
    if annotation is SecretStr
]
SECTIONS: list[type[BaseModel]] = [
    annotation
    for _path, annotation in _model_fields(Settings)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel)
]


def _nested(path: str, value: object) -> dict[str, object]:
    """Return keyword arguments setting the dotted ``path`` to ``value``."""
    head, *rest = path.split(".")
    return {head: _nested(".".join(rest), value) if rest else value}


def _secret() -> str:
    """Return a synthetic secret."""
    return f"settings-secret-{token_hex(8)}"


def _printed(error: BaseException) -> str:
    """Return the error as the interpreter prints it, with its chain."""
    return "".join(traceback.format_exception(error))


def test_credential_settings_are_enumerated() -> None:
    """The enumeration finds the credential settings, nested ones included."""
    assert_that(SECRET_PATHS).contains(
        "database.url",
        "rate_limit.redis_url",
        "transcripts.encryption_key",
        "auth.smtp_password",
        "enrichment.tmdb.api_key",
        "ops_api_key",
    )


@pytest.mark.parametrize("path", SECRET_PATHS)
def test_invalid_credential_names_path_and_type_only(path: str) -> None:
    """A credential of the wrong type is reported without its input."""
    secret = _secret()

    with pytest.raises(ValidationError) as caught:
        Settings(**_nested(path, [secret]))

    assert_that(str(caught.value)).contains(f"\n{path}\n")
    assert_that(str(caught.value)).contains("[type=string_type]")
    assert_that(_printed(caught.value)).does_not_contain(secret)
    assert_that(repr(caught.value)).does_not_contain(secret)


@pytest.mark.parametrize(
    "path",
    [
        ".".join(path)
        for path, annotation in _model_fields(Settings)
        if annotation in SECTIONS
    ],
)
def test_section_given_a_string_names_path_and_type_only(path: str) -> None:
    """A whole section given as a string (a URL, a key) hides that string."""
    secret = _secret()

    with pytest.raises(ValidationError) as caught:
        Settings(**_nested(path, secret))

    assert_that(str(caught.value)).contains(f"\n{path}\n")
    assert_that(str(caught.value)).contains("[type=model_type]")
    assert_that(_printed(caught.value)).does_not_contain(secret)


def test_every_section_hides_inputs_on_its_own() -> None:
    """Each nested model hides inputs when it is validated outside Settings."""
    assert_that(SECTIONS).is_not_empty()
    for section in SECTIONS:
        assert_that(issubclass(section, SettingsSection)).described_as(
            section.__name__,
        ).is_true()
        secret = _secret()
        with pytest.raises(ValidationError) as caught:
            section.model_validate(secret)
        assert_that(_printed(caught.value)).does_not_contain(secret)
        assert_that(str(caught.value)).contains("[type=model_type]")


def test_settings_resolution_installs_the_boundary_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary holds before the settings library reads the environment."""
    calls: list[str] = []
    resolved = Settings()

    def install() -> None:
        """Record the boundary installation."""
        calls.append("boundary")

    def resolve() -> Settings:
        """Record the settings resolution."""
        calls.append("settings")
        return resolved

    monkeypatch.setattr(config, "install_log_boundaries", install)
    monkeypatch.setattr(config, "Settings", resolve)
    get_settings.cache_clear()
    try:
        assert_that(get_settings()).is_same_as(resolved)
    finally:
        get_settings.cache_clear()

    assert_that(calls).is_equal_to(["boundary", "settings"])


@pytest.mark.parametrize(
    ("template", "failure"),
    [
        # An unescaped "@" and ":" make the parser read the password's rest
        # as the port; its own error quotes it.
        ("postgresql+psycopg://podex:pw@part:{secret}@db.invalid/podex", "ValueError"),
        ("nodialect://podex:{secret}@db.invalid/podex", "NoSuchModuleError"),
        ("not a database URL {secret}", "ArgumentError"),
    ],
)
def test_unusable_url_reports_error_class_only(template: str, failure: str) -> None:
    """Building an engine from an unusable URL names the class, not the URL."""
    secret = _secret()
    url = template.format(secret=secret)

    with pytest.raises(DatabaseUrlError) as caught:
        without_url_text(lambda: create_engine(url))

    assert_that(str(caught.value)).contains(f"is not usable: {failure}.")
    assert_that(caught.value.__cause__).is_none()
    assert_that(caught.value.__context__).is_none()
    assert_that(_printed(caught.value)).does_not_contain(secret)
    assert_that(_printed(caught.value)).does_not_contain("podex:pw")


def test_unusable_url_dialect_check_reports_error_class_only() -> None:
    """Parsing a URL and loading its dialect fail the same way."""
    secret = _secret()

    with pytest.raises(DatabaseUrlError) as caught:
        without_url_text(
            lambda: make_url(
                f"nodialect://podex:{secret}@db.invalid/podex"
            ).get_dialect(),
        )

    assert_that(str(caught.value)).contains("is not usable: NoSuchModuleError.")
    assert_that(_printed(caught.value)).does_not_contain(secret)


def test_usable_url_builds_as_before() -> None:
    """A usable URL returns what the build returns."""
    engine = without_url_text(lambda: create_engine("sqlite://"))

    assert_that(engine.dialect.name).is_equal_to("sqlite")
    engine.dispose()


def test_other_failures_pass_through() -> None:
    """Failures that are not URL errors propagate unchanged."""
    error = RuntimeError("not a URL failure")

    def build() -> None:
        """Fail as something other than the URL would."""
        raise error

    with pytest.raises(RuntimeError) as caught:
        without_url_text(build)

    assert_that(caught.value).is_same_as(error)
