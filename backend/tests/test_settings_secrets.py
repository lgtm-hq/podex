"""Secret settings are masked types, and no rendering of settings shows them."""

import re
from collections.abc import Iterator

import pytest
from assertpy import assert_that
from pydantic import BaseModel, SecretStr

from podex.config import Settings
from podex.services.smtp_delivery import SmtpDelivery
from tests.secret_support import (
    SECRET_ENVIRONMENT,
    leaked,
    load_secret_settings,
)

_SECRET_NAME = re.compile(r"key|secret|password|token|credential|passphrase", re.I)
"""Field names that suggest a credential."""

_CREDENTIAL_URLS = frozenset({"database.url", "rate_limit.redis_url"})
"""URL fields whose value can embed a password."""


def _fields(model: type[BaseModel], prefix: str = "") -> Iterator[tuple[str, object]]:
    """Yield the dotted path and annotation of every field, recursively."""
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from _fields(annotation, f"{path}.")
        else:
            yield path, annotation


def _secret_fields() -> list[tuple[str, object]]:
    """Return the settings fields that hold, or can hold, a credential."""
    return [
        (path, annotation)
        for path, annotation in _fields(Settings)
        if _SECRET_NAME.search(path.rsplit(".", 1)[-1]) or path in _CREDENTIAL_URLS
    ]


def _models(model: BaseModel) -> Iterator[BaseModel]:
    """Yield a settings model and every nested settings model."""
    yield model
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            yield from _models(value)


def _environment_name(path: str) -> str:
    """Return the environment variable for a dotted settings path."""
    return "PODEX_" + "__".join(path.split(".")).upper()


def test_every_secret_named_field_is_masked() -> None:
    """A credential field declared as a plain string fails here."""
    fields = _secret_fields()

    assert_that(fields).is_not_empty()
    plain = [path for path, annotation in fields if annotation is not SecretStr]
    assert_that(plain).is_empty()


def test_every_secret_field_has_a_synthetic_value() -> None:
    """The leak tests cover every secret field, including any added later."""
    names = {_environment_name(path) for path, _annotation in _secret_fields()}

    assert_that(names).is_equal_to(set(SECRET_ENVIRONMENT))


def test_secret_values_read_back_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Masking changes rendering only; each use still reads the exact value."""
    settings = load_secret_settings(monkeypatch)

    for path, _annotation in _secret_fields():
        value: object = settings
        for part in path.split("."):
            value = getattr(value, part)
        if not isinstance(value, SecretStr):
            raise AssertionError(f"{path} is not masked")
        assert_that(value.get_secret_value()).is_equal_to(
            SECRET_ENVIRONMENT[_environment_name(path)],
        )


def test_settings_renderings_mask_every_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``repr``, ``str`` and serialisation of every settings model are masked."""
    settings = load_secret_settings(monkeypatch)

    for model in _models(settings):
        renderings = (
            repr(model),
            str(model),
            f"{model}",
            str(model.model_dump()),
            model.model_dump_json(),
        )
        for rendering in renderings:
            assert_that(leaked(rendering)).described_as(
                type(model).__name__,
            ).is_empty()


def test_smtp_adapter_receives_but_never_renders_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SMTP adapter reads the real password and keeps it out of its repr."""
    settings = load_secret_settings(monkeypatch)

    delivery = SmtpDelivery.from_settings(settings)

    assert_that(delivery.password).is_equal_to(
        SECRET_ENVIRONMENT["PODEX_AUTH__SMTP_PASSWORD"],
    )
    assert_that(leaked(repr(delivery))).is_empty()
