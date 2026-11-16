"""Settings validation errors print no configured value.

A failed start-up is printed by the interpreter, not by logging, so the
exception text itself must be safe: a settings validation error's printed text
names the field path and error type only. The error object itself still holds
the input (``errors()``, ``json()``); nothing prints those at start-up.
Settings resolution installs the log boundary before it reads the
environment. Unusable URLs and DSNs are covered in
``tests/test_unusable_settings.py``.
"""

import traceback
from collections.abc import Iterator
from secrets import token_hex

import pytest
from assertpy import assert_that
from pydantic import BaseModel, SecretStr, ValidationError

from podex import config
from podex.config import Settings, SettingsSection, get_settings


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
