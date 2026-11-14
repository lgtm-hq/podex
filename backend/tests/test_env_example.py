"""Keep ``.env.example`` and ``docs/configuration.md`` in step with ``Settings``.

The checks use setting names only: a field counts as a nested settings group
when its annotation is a Pydantic model, and every other field is one
environment variable. Changing a field's value type does not affect them.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from pydantic import AliasChoices, BaseModel, Field
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from podex.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"

# A whole ``PODEX_*`` name: not preceded by a name character, ending in a
# letter or digit, and not followed by a name character or a ``*`` wildcard.
# This skips prose such as ``PODEX_*`` or ``PODEX_<GROUP>__<FIELD>`` and never
# reports the prefix of a longer name.
_DOC_NAME = re.compile(r"(?<![A-Za-z0-9_])PODEX_[A-Z0-9_]*[A-Z0-9](?![A-Za-z0-9_*])")
_ENV_NAME = re.compile(r"PODEX_[A-Z0-9_]+")
_EXPORT = "export "


def _field_aliases(field: FieldInfo) -> list[str]:
    """Return the environment names a field's validation alias selects."""
    alias = field.validation_alias
    if alias is None:
        return []
    if isinstance(alias, str):
        return [alias]
    if isinstance(alias, AliasChoices) and all(
        isinstance(choice, str) for choice in alias.choices
    ):
        return [str(choice) for choice in alias.choices]
    raise TypeError(f"Unsupported validation alias for an environment name: {alias}")


def _field_variables(
    name: str,
    field: FieldInfo,
    *,
    parent: str | None,
    env_prefix: str,
    delimiter: str,
) -> list[str]:
    """Return the variable names that select one field.

    A top-level alias is the whole variable name, without the prefix; a nested
    alias replaces that field's segment under its parent's name.
    """
    aliases = _field_aliases(field)
    if parent is None:
        if aliases:
            return [alias.upper() for alias in aliases]
        return [f"{env_prefix}{name.upper()}"]
    return [f"{parent}{delimiter}{segment.upper()}" for segment in aliases or [name]]


def _nested_group(field: FieldInfo) -> type[BaseModel] | None:
    """Return the field's model class when it is a nested settings group."""
    annotation = field.annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _variable_names(
    model: type[BaseModel],
    *,
    parent: str | None,
    env_prefix: str,
    delimiter: str,
) -> Iterator[str]:
    """Yield the environment variable name of every leaf setting in ``model``."""
    for name, field in model.model_fields.items():
        group = _nested_group(field)
        for variable in _field_variables(
            name,
            field,
            parent=parent,
            env_prefix=env_prefix,
            delimiter=delimiter,
        ):
            if group is not None:
                yield from _variable_names(
                    group,
                    parent=variable,
                    env_prefix=env_prefix,
                    delimiter=delimiter,
                )
            else:
                yield variable


def _settings_variables(model: type[BaseSettings]) -> set[str]:
    """Return every environment variable that ``model`` reads."""
    env_prefix = model.model_config.get("env_prefix") or ""
    delimiter = model.model_config.get("env_nested_delimiter") or ""
    return set(
        _variable_names(
            model,
            parent=None,
            env_prefix=env_prefix,
            delimiter=delimiter,
        ),
    )


def _env_assignments(text: str) -> set[str]:
    """Return the ``PODEX_*`` names assigned in dotenv text.

    Accepts an optional ``export`` prefix and skips blank and comment lines.
    """
    names: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(_EXPORT):
            line = line.removeprefix(_EXPORT).lstrip()
        name, separator, _value = line.partition("=")
        name = name.strip()
        if separator and _ENV_NAME.fullmatch(name):
            names.add(name)
    return names


def _documented_names(text: str) -> set[str]:
    """Return every whole ``PODEX_*`` name mentioned in Markdown text."""
    return set(_DOC_NAME.findall(text))


class _AliasedGroup(BaseModel):
    """Nested group with one renamed field."""

    renamed: str = Field(default="", validation_alias="OLD_NAME")
    kept: str = ""


class _AliasedSettings(BaseSettings):
    """Throwaway settings model exercising validation aliases."""

    model_config = SettingsConfigDict(env_prefix="PODEX_", env_nested_delimiter="__")

    plain: str = ""
    token: str = Field(default="", validation_alias="PODEX_LEGACY_TOKEN")
    choice: str = Field(
        default="",
        validation_alias=AliasChoices("PODEX_CHOICE_A", "PODEX_CHOICE_B"),
    )
    group: _AliasedGroup = Field(default_factory=_AliasedGroup)


def test_settings_walk_reaches_nested_fields() -> None:
    """The name walk descends into nested groups instead of stopping at them."""
    variables = _settings_variables(Settings)

    assert_that(Settings.model_config.get("env_prefix")).is_equal_to("PODEX_")
    assert_that(Settings.model_config.get("env_nested_delimiter")).is_equal_to("__")
    assert_that(variables).contains(
        "PODEX_APP_NAME",
        "PODEX_DATABASE__URL",
        "PODEX_ENRICHMENT__TMDB__API_KEY",
    )
    assert_that(variables).does_not_contain(
        "PODEX_DATABASE",
        "PODEX_ENRICHMENT",
        "PODEX_ENRICHMENT__TMDB",
    )


def test_settings_walk_uses_validation_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aliased fields are named by their alias, as pydantic-settings reads them."""
    variables = _settings_variables(_AliasedSettings)

    assert_that(variables).is_equal_to(
        {
            "PODEX_PLAIN",
            "PODEX_LEGACY_TOKEN",
            "PODEX_CHOICE_A",
            "PODEX_CHOICE_B",
            "PODEX_GROUP__OLD_NAME",
            "PODEX_GROUP__KEPT",
        },
    )
    monkeypatch.setenv("PODEX_LEGACY_TOKEN", "token-value")
    monkeypatch.setenv("PODEX_CHOICE_B", "choice-value")
    monkeypatch.setenv("PODEX_GROUP__OLD_NAME", "renamed-value")
    loaded = _AliasedSettings()
    assert_that(loaded.token).is_equal_to("token-value")
    assert_that(loaded.choice).is_equal_to("choice-value")
    assert_that(loaded.group.renamed).is_equal_to("renamed-value")


def test_env_assignments_accept_export_prefix() -> None:
    """``export`` lines count; comments, blanks and other names do not."""
    text = (
        "export PODEX_DEBUG=false\n"
        "  PODEX_APP_NAME=podex\n"
        "# PODEX_COMMENTED=1\n"
        "\n"
        "OTHER_NAME=1\n"
    )

    assert_that(_env_assignments(text)).is_equal_to({"PODEX_DEBUG", "PODEX_APP_NAME"})


def test_documented_names_ignore_backticks() -> None:
    """Names count with or without backticks, so an unformatted name is seen."""
    text = "Set PODEX_DEBUG or `PODEX_APP_NAME`. PODEX_NOT_A_SETTING is unknown."

    found = _documented_names(text)

    assert_that(found).is_equal_to(
        {"PODEX_DEBUG", "PODEX_APP_NAME", "PODEX_NOT_A_SETTING"},
    )
    assert_that(found - _settings_variables(Settings)).is_equal_to(
        {"PODEX_NOT_A_SETTING"},
    )


def test_documented_names_skip_wildcards_and_prefixes() -> None:
    """Wildcards and placeholders are prose, and a longer name stays whole."""
    text = (
        "Every `PODEX_*` variable, PODEX_FEED_HTTP__* limits, "
        "`PODEX_<GROUP>__<FIELD>`, and PODEX_DATABASE__URLS."
    )

    assert_that(_documented_names(text)).is_equal_to({"PODEX_DATABASE__URLS"})


def test_env_example_lists_every_setting() -> None:
    """Every ``Settings`` field has a line in ``.env.example`` and vice versa."""
    settings_variables = _settings_variables(Settings)
    example_variables = _env_assignments(ENV_EXAMPLE.read_text(encoding="utf-8"))

    assert_that(sorted(settings_variables - example_variables)).is_empty()
    assert_that(sorted(example_variables - settings_variables)).is_empty()


def test_configuration_doc_lists_every_setting() -> None:
    """Every ``Settings`` field is documented, and no stale name is."""
    settings_variables = _settings_variables(Settings)
    documented_variables = _documented_names(
        CONFIGURATION_DOC.read_text(encoding="utf-8"),
    )

    assert_that(sorted(settings_variables - documented_variables)).is_empty()
    assert_that(sorted(documented_variables - settings_variables)).is_empty()
