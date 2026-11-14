"""Keep ``.env.example`` and ``docs/configuration.md`` in step with ``Settings``.

The checks use setting names only: a field counts as a nested settings group
when its annotation is a Pydantic model, and every other field is one
environment variable. Changing a field's value type does not affect them.
"""

import re
from collections.abc import Iterator
from pathlib import Path

from assertpy import assert_that
from pydantic import BaseModel

from podex.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "configuration.md"

_ENV_ASSIGNMENT = re.compile(r"^(PODEX_[A-Z0-9_]+)=", re.MULTILINE)
_DOC_NAME = re.compile(r"`(PODEX_[A-Z0-9_]+)`")


def _variable_names(
    model: type[BaseModel],
    *,
    prefix: str,
    delimiter: str,
) -> Iterator[str]:
    """Yield the environment variable name of every leaf setting in ``model``."""
    for name, field in model.model_fields.items():
        variable = f"{prefix}{name.upper()}"
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            yield from _variable_names(
                annotation,
                prefix=f"{variable}{delimiter}",
                delimiter=delimiter,
            )
        else:
            yield variable


def _settings_variables() -> set[str]:
    """Return every environment variable that ``Settings`` reads."""
    prefix = Settings.model_config.get("env_prefix") or ""
    delimiter = Settings.model_config.get("env_nested_delimiter") or ""
    assert_that(prefix).is_equal_to("PODEX_")
    assert_that(delimiter).is_equal_to("__")
    return set(_variable_names(Settings, prefix=prefix, delimiter=delimiter))


def _env_example_variables() -> set[str]:
    """Return the ``PODEX_*`` names assigned in ``.env.example``."""
    return set(_ENV_ASSIGNMENT.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def _documented_variables() -> set[str]:
    """Return the ``PODEX_*`` names quoted in ``docs/configuration.md``."""
    return set(_DOC_NAME.findall(CONFIGURATION_DOC.read_text(encoding="utf-8")))


def test_settings_walk_reaches_nested_fields() -> None:
    """The name walk descends into nested groups instead of stopping at them."""
    variables = _settings_variables()

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


def test_env_example_lists_every_setting() -> None:
    """Every ``Settings`` field has a line in ``.env.example`` and vice versa."""
    settings_variables = _settings_variables()
    example_variables = _env_example_variables()

    assert_that(sorted(settings_variables - example_variables)).is_empty()
    assert_that(sorted(example_variables - settings_variables)).is_empty()


def test_configuration_doc_lists_every_setting() -> None:
    """Every ``Settings`` field is documented, and no stale name is."""
    settings_variables = _settings_variables()
    documented_variables = _documented_variables()

    assert_that(sorted(settings_variables - documented_variables)).is_empty()
    assert_that(sorted(documented_variables - settings_variables)).is_empty()
