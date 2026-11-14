"""Keep ``.env.example`` and ``docs/configuration.md`` in step with ``Settings``.

The checks use setting names only: a field counts as a nested settings group
when its annotation is a Pydantic model, and every other field is one
environment variable. Changing a field's value type does not affect them.

The helper tests build their settings models with ``create_model`` instead of a
class statement, so type checking does not depend on the third-party base
classes being resolvable.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from pydantic import AliasChoices, BaseModel, Field, create_model
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings

from podex.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"

_NAME_START = r"(?<![A-Za-z0-9_])"
# Not followed by a name character or a ``*`` wildcard, so prose such as
# ``PODEX_*`` is skipped and a longer name is never reported as its prefix.
_NAME_END = r"(?![A-Za-z0-9_*])"
# A whole ``PODEX_*`` name ending in a letter or digit; placeholders such as
# ``PODEX_<GROUP>__<FIELD>`` do not match.
_DOC_NAME = re.compile(rf"{_NAME_START}PODEX_[A-Z0-9_]*[A-Z0-9]{_NAME_END}")
# Markdown emphasis (``*``, ``**``, ``***``, ``_``, ``__``) wrapped directly
# around a name, which would otherwise look like a wildcard or a name character.
_EMPHASIS = re.compile(
    r"(?<![A-Za-z0-9_*])(\*{1,3}|_{1,2})([A-Za-z][A-Za-z0-9_]*[A-Za-z0-9])\1"
    r"(?![A-Za-z0-9_*])",
)
_ASSIGNED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
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
    """Return every variable name assigned in dotenv text.

    Accepts an optional ``export`` prefix and skips blank and comment lines.
    """
    names: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(_EXPORT):
            line = line.removeprefix(_EXPORT).lstrip()
        name, separator, _value = line.partition("=")
        name = name.strip()
        if separator and _ASSIGNED_NAME.fullmatch(name):
            names.add(name)
    return names


def _strip_emphasis(text: str) -> str:
    """Remove Markdown emphasis wrapped directly around a name."""
    return _EMPHASIS.sub(r"\2", text)


def _documented_names(text: str) -> set[str]:
    """Return every whole ``PODEX_*`` name mentioned in Markdown text."""
    return set(_DOC_NAME.findall(_strip_emphasis(text)))


def _mentioned(names: set[str], text: str) -> set[str]:
    """Return the members of ``names`` mentioned as whole names in ``text``."""
    plain = _strip_emphasis(text)
    return {
        name
        for name in names
        if re.search(f"{_NAME_START}{re.escape(name)}{_NAME_END}", plain)
    }


def _env_gaps(expected: set[str], text: str) -> tuple[set[str], set[str]]:
    """Return expected names missing from dotenv text, and unknown names in it."""
    assigned = _env_assignments(text)
    return expected - assigned, assigned - expected


def _doc_gaps(expected: set[str], text: str) -> tuple[set[str], set[str]]:
    """Return expected names missing from a document, and unknown ``PODEX_*``."""
    return expected - _mentioned(expected, text), _documented_names(text) - expected


def _aliased_settings_model() -> type[BaseSettings]:
    """Build a throwaway settings model whose fields exercise every alias form."""
    group = create_model(
        "AliasedGroup",
        renamed=(str, Field(default="", validation_alias="OLD_NAME")),
        kept=(str, ""),
    )
    model: type[BaseSettings] = create_model(
        "AliasedSettings",
        __base__=BaseSettings,
        __cls_kwargs__={"env_prefix": "PODEX_", "env_nested_delimiter": "__"},
        plain=(str, ""),
        renamed=(str, Field(default="", validation_alias="PODEX_LEGACY_TOKEN")),
        legacy=(str, Field(default="", validation_alias="LEGACY_NAME")),
        choice=(
            str,
            Field(
                default="",
                validation_alias=AliasChoices("PODEX_CHOICE_A", "PODEX_CHOICE_B"),
            ),
        ),
        group=(group, Field(default_factory=group)),
    )
    return model


_ALIASED_VARIABLES = {
    "PODEX_PLAIN",
    "PODEX_LEGACY_TOKEN",
    "LEGACY_NAME",
    "PODEX_CHOICE_A",
    "PODEX_CHOICE_B",
    "PODEX_GROUP__OLD_NAME",
    "PODEX_GROUP__KEPT",
}


def test_settings_walk_uses_validation_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fields are named as pydantic-settings reads them, including aliases."""
    model = _aliased_settings_model()

    assert_that(_settings_variables(model)).is_equal_to(_ALIASED_VARIABLES)
    monkeypatch.setenv("PODEX_PLAIN", "plain-value")
    monkeypatch.setenv("PODEX_LEGACY_TOKEN", "renamed-top-value")
    monkeypatch.setenv("LEGACY_NAME", "legacy-value")
    monkeypatch.setenv("PODEX_CHOICE_B", "choice-value")
    monkeypatch.setenv("PODEX_GROUP__OLD_NAME", "renamed-value")
    monkeypatch.setenv("PODEX_GROUP__KEPT", "kept-value")
    assert_that(model().model_dump()).is_equal_to(
        {
            "plain": "plain-value",
            "renamed": "renamed-top-value",
            "legacy": "legacy-value",
            "choice": "choice-value",
            "group": {"renamed": "renamed-value", "kept": "kept-value"},
        },
    )


def test_env_assignments_accept_export_prefix() -> None:
    """``export`` lines count; comments and blank lines do not."""
    text = (
        "export PODEX_PLAIN=value\n"
        "  PODEX_LEGACY_TOKEN=value\n"
        "LEGACY_NAME=\n"
        "# PODEX_COMMENTED=1\n"
        "\n"
    )

    assert_that(_env_assignments(text)).is_equal_to(
        {"PODEX_PLAIN", "PODEX_LEGACY_TOKEN", "LEGACY_NAME"},
    )


def test_documented_names_ignore_backticks() -> None:
    """Names count with or without backticks, so an unformatted name is seen."""
    expected = _settings_variables(_aliased_settings_model())
    text = "Set PODEX_PLAIN or `PODEX_LEGACY_TOKEN`. PODEX_NOT_A_SETTING is unknown."

    assert_that(_documented_names(text)).is_equal_to(
        {"PODEX_PLAIN", "PODEX_LEGACY_TOKEN", "PODEX_NOT_A_SETTING"},
    )
    assert_that(_mentioned(expected, text)).is_equal_to(
        {"PODEX_PLAIN", "PODEX_LEGACY_TOKEN"},
    )
    assert_that(_doc_gaps(expected, text)[1]).is_equal_to({"PODEX_NOT_A_SETTING"})


def test_documented_names_strip_emphasis() -> None:
    """Emphasis around a name is Markdown, not a wildcard or name character."""
    expected = _settings_variables(_aliased_settings_model())

    for marker in ("**", "*", "***", "__", "_"):
        text = f"Set {marker}PODEX_PLAIN{marker} and {marker}PODEX_UNKNOWN{marker}."

        assert_that(_documented_names(text)).described_as(marker).is_equal_to(
            {"PODEX_PLAIN", "PODEX_UNKNOWN"},
        )
        assert_that(_mentioned(expected, text)).described_as(marker).is_equal_to(
            {"PODEX_PLAIN"},
        )
        assert_that(_doc_gaps(expected, text)[1]).described_as(marker).is_equal_to(
            {"PODEX_UNKNOWN"},
        )


def test_documented_names_skip_wildcards_and_prefixes() -> None:
    """Wildcards and placeholders are prose, and a longer name stays whole."""
    text = (
        "Every `PODEX_*` variable, PODEX_FEED_HTTP__* limits, **PODEX_*** values, "
        "`PODEX_<GROUP>__<FIELD>`, and PODEX_PLAIN_EXTRA."
    )

    assert_that(_documented_names(text)).is_equal_to({"PODEX_PLAIN_EXTRA"})
    assert_that(_mentioned({"PODEX_PLAIN"}, text)).is_empty()


def test_inventory_requires_unprefixed_aliases() -> None:
    """An alias without the ``PODEX_`` prefix must still be listed and documented."""
    expected = _settings_variables(_aliased_settings_model())
    others = sorted(expected - {"LEGACY_NAME"})
    env_without = "".join(f"{name}=\n" for name in others)
    doc_without = " ".join(f"`{name}`" for name in others)

    assert_that(_env_gaps(expected, env_without)).is_equal_to(
        ({"LEGACY_NAME"}, set()),
    )
    assert_that(_doc_gaps(expected, doc_without)).is_equal_to(
        ({"LEGACY_NAME"}, set()),
    )
    assert_that(_env_gaps(expected, f"{env_without}LEGACY_NAME=\n")).is_equal_to(
        (set(), set()),
    )
    assert_that(_doc_gaps(expected, f"{doc_without} `LEGACY_NAME`")).is_equal_to(
        (set(), set()),
    )


def test_settings_use_documented_prefix_and_delimiter() -> None:
    """The documented ``PODEX_<GROUP>__<FIELD>`` grammar matches ``Settings``."""
    assert_that(Settings.model_config.get("env_prefix")).is_equal_to("PODEX_")
    assert_that(Settings.model_config.get("env_nested_delimiter")).is_equal_to("__")


def test_env_example_lists_every_setting() -> None:
    """Every ``Settings`` field has a line in ``.env.example`` and vice versa."""
    missing, unknown = _env_gaps(
        _settings_variables(Settings),
        ENV_EXAMPLE.read_text(encoding="utf-8"),
    )

    assert_that(sorted(missing)).is_empty()
    assert_that(sorted(unknown)).is_empty()


def test_configuration_doc_lists_every_setting() -> None:
    """Every ``Settings`` field is documented, and no stale name is."""
    missing, unknown = _doc_gaps(
        _settings_variables(Settings),
        CONFIGURATION_DOC.read_text(encoding="utf-8"),
    )

    assert_that(sorted(missing)).is_empty()
    assert_that(sorted(unknown)).is_empty()
