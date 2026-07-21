"""Tests for packaged enrichment data tables (issue #348 migration)."""

from __future__ import annotations

from importlib.resources import files

import pytest
from assertpy import assert_that

from podex.models.media import MediaType
from podex.services.enrichment.data import (
    ALBUM_CATEGORY_PATTERNS,
    CATEGORY_TYPE_PATTERNS,
    STOP_WORDS,
    TYPE_NEGATIVE_SIGNALS,
    TYPE_POSITIVE_SIGNALS,
)

# Snapshots of the pre-migration inline literals (origin/main @ 9110253).
# Kept here so the migration PR can prove TOML content is identical.

_EXPECTED_POSITIVE: dict[MediaType, list[str]] = {
    MediaType.STUDY: [
        "study",
        "experiment",
        "research",
        "trial",
        "clinical",
        "medical",
        "scientific",
        "investigation",
        "conducted",
        "participants",
        "subjects",
        "findings",
        "published",
        "journal",
    ],
    MediaType.MOVIE: [
        "film",
        "movie",
        "directed by",
        "starring",
        "produced by",
        "screenplay",
        "box office",
        "cinema",
        "released",
    ],
    MediaType.TV_SHOW: [
        "television",
        "tv series",
        "sitcom",
        "episodes",
        "seasons",
        "aired",
        "broadcast",
        "network",
        "showrunner",
    ],
    MediaType.DOCUMENTARY: [
        "documentary",
        "film",
        "directed",
        "footage",
        "narrator",
    ],
    MediaType.BOOK: [
        "novel",
        "book",
        "author",
        "written by",
        "published",
        "literary",
        "chapter",
        "pages",
        "isbn",
    ],
    MediaType.PERSON: [
        "born",
        "died",
        "is a",
        "was a",
        "known for",
        "career",
        "biography",
    ],
    MediaType.PLACE: [
        "located",
        "city",
        "town",
        "country",
        "region",
        "population",
        "geography",
        "coordinates",
    ],
    MediaType.PODCAST: [
        "podcast",
        "hosted by",
        "episodes",
        "audio",
    ],
}

_EXPECTED_NEGATIVE: dict[MediaType, list[str]] = {
    MediaType.STUDY: [
        "album",
        "song",
        "band",
        "singer",
        "musician",
        "film",
        "movie",
        "novel",
        "book",
        "video game",
        "discography",
        "studio album",
        "single",
        "ep",
        "soundtrack",
    ],
    MediaType.MOVIE: [
        "album",
        "song",
        "novel",
        "book",
        "video game",
        "tv series",
        "television series",
    ],
    MediaType.TV_SHOW: [
        "album",
        "song",
        "novel",
        "film",
        "movie",
        "video game",
    ],
    MediaType.BOOK: [
        "album",
        "song",
        "film",
        "movie",
        "tv series",
        "video game",
    ],
    MediaType.PERSON: [
        "album",
        "film",
        "novel",
        "song",
        "is a city",
        "is a town",
        "is a country",
    ],
    MediaType.PLACE: [
        "is an album",
        "is a film",
        "is a novel",
        "is a song",
        "born",
    ],
}

_EXPECTED_CATEGORY: dict[MediaType, list[str]] = {
    MediaType.STUDY: [
        r"medical.*stud",
        r"clinical.*trial",
        r"experiment",
        r"research",
        r"health.*scandal",
        r"medical.*ethic",
        r"scientific.*misconduct",
    ],
    MediaType.MOVIE: [
        r"\d{4}.*film",
        r"film.*by",
        r"english.*film",
        r"american.*film",
    ],
    MediaType.TV_SHOW: [
        r"television.*series",
        r"tv.*series",
        r"\d{4}.*television",
        r"american.*television",
    ],
    MediaType.BOOK: [
        r"\d{4}.*novel",
        r"book.*by",
        r"fiction",
    ],
    MediaType.PERSON: [
        r"living.*people",
        r"\d{4}.*birth",
        r"\d{4}.*death",
        r"people.*from",
    ],
}

_EXPECTED_ALBUM: list[str] = [
    r"\d{4}.*album",
    r"album.*by",
    r"studio.*album",
]

_EXPECTED_STOP_WORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
    },
)

_SIGNAL_TABLES: list[
    tuple[str, dict[MediaType, list[str]], dict[MediaType, list[str]]]
] = [
    ("positive_signals", TYPE_POSITIVE_SIGNALS, _EXPECTED_POSITIVE),
    ("negative_signals", TYPE_NEGATIVE_SIGNALS, _EXPECTED_NEGATIVE),
    ("category_patterns", CATEGORY_TYPE_PATTERNS, _EXPECTED_CATEGORY),
]


def test_packaged_toml_resources_are_findable() -> None:
    """importlib.resources can locate both enrichment TOML files."""
    data = files("podex.services.enrichment.data")
    for name in ("wikipedia_signals.toml", "stop_words.toml"):
        path = data.joinpath(name)
        assert_that(path.is_file()).is_true()
        assert_that(path.read_text(encoding="utf-8")).is_not_empty()


@pytest.mark.parametrize(
    ("table_name", "loaded", "expected"),
    _SIGNAL_TABLES,
    ids=[name for name, _, _ in _SIGNAL_TABLES],
)
def test_signal_table_matches_pre_migration_literals(
    table_name: str,
    loaded: dict[MediaType, list[str]],
    expected: dict[MediaType, list[str]],
) -> None:
    """Loaded Wikipedia signal maps equal the former inline literals."""
    del table_name
    assert_that(set(loaded)).is_equal_to(set(expected))
    for media_type, values in expected.items():
        assert_that(loaded[media_type]).is_equal_to(values)
        assert_that(len(loaded[media_type])).is_equal_to(len(set(loaded[media_type])))


def test_album_category_patterns_match_pre_migration_literals() -> None:
    """Album category patterns equal the former inline list."""
    assert_that(ALBUM_CATEGORY_PATTERNS).is_equal_to(_EXPECTED_ALBUM)
    assert_that(len(ALBUM_CATEGORY_PATTERNS)).is_equal_to(
        len(set(ALBUM_CATEGORY_PATTERNS)),
    )


def test_stop_words_match_pre_migration_literals() -> None:
    """STOP_WORDS frozenset equals the former inline set (42 English words)."""
    assert_that(STOP_WORDS).is_instance_of(frozenset)
    assert_that(STOP_WORDS).is_equal_to(_EXPECTED_STOP_WORDS)
    assert_that(len(STOP_WORDS)).is_equal_to(42)


@pytest.mark.parametrize(
    ("media_type",),
    [(mt,) for mt in _EXPECTED_POSITIVE],
    ids=[mt.value for mt in _EXPECTED_POSITIVE],
)
def test_positive_signals_cover_expected_media_types(media_type: MediaType) -> None:
    """Every media type that previously had positive signals is still keyed."""
    assert_that(TYPE_POSITIVE_SIGNALS).contains_key(media_type)
    assert_that(TYPE_POSITIVE_SIGNALS[media_type]).is_not_empty()


@pytest.mark.parametrize(
    ("media_type",),
    [(mt,) for mt in _EXPECTED_NEGATIVE],
    ids=[mt.value for mt in _EXPECTED_NEGATIVE],
)
def test_negative_signals_cover_expected_media_types(media_type: MediaType) -> None:
    """Every media type that previously had negative signals is still keyed."""
    assert_that(TYPE_NEGATIVE_SIGNALS).contains_key(media_type)
    assert_that(TYPE_NEGATIVE_SIGNALS[media_type]).is_not_empty()


@pytest.mark.parametrize(
    ("media_type",),
    [(mt,) for mt in _EXPECTED_CATEGORY],
    ids=[mt.value for mt in _EXPECTED_CATEGORY],
)
def test_category_patterns_cover_expected_media_types(media_type: MediaType) -> None:
    """Every media type that previously had category patterns is still keyed."""
    assert_that(CATEGORY_TYPE_PATTERNS).contains_key(media_type)
    assert_that(CATEGORY_TYPE_PATTERNS[media_type]).is_not_empty()
