"""Packaged enrichment data tables (Wikipedia signals, stop words)."""

from __future__ import annotations

import tomllib
from importlib.resources import files
from typing import Any, cast

from podex.models.media import MediaType

_DATA = files("podex.services.enrichment.data")


def _load_toml(name: str) -> dict[str, Any]:
    raw = _DATA.joinpath(name).read_text(encoding="utf-8")
    return tomllib.loads(raw)


def _media_type_lists(
    raw: dict[str, list[str]],
) -> dict[MediaType, list[str]]:
    return {MediaType(key): list(values) for key, values in raw.items()}


_signals = _load_toml("wikipedia_signals.toml")

TYPE_POSITIVE_SIGNALS: dict[MediaType, list[str]] = _media_type_lists(
    cast(dict[str, list[str]], _signals["positive_signals"]),
)
TYPE_NEGATIVE_SIGNALS: dict[MediaType, list[str]] = _media_type_lists(
    cast(dict[str, list[str]], _signals["negative_signals"]),
)
CATEGORY_TYPE_PATTERNS: dict[MediaType, list[str]] = _media_type_lists(
    cast(dict[str, list[str]], _signals["category_patterns"]),
)
ALBUM_CATEGORY_PATTERNS: list[str] = list(
    cast(list[str], _signals["album_category_patterns"]),
)

_stop_words = _load_toml("stop_words.toml")
STOP_WORDS: frozenset[str] = frozenset(
    cast(list[str], _stop_words["english"]),
)

__all__ = [
    "ALBUM_CATEGORY_PATTERNS",
    "CATEGORY_TYPE_PATTERNS",
    "STOP_WORDS",
    "TYPE_NEGATIVE_SIGNALS",
    "TYPE_POSITIVE_SIGNALS",
]
