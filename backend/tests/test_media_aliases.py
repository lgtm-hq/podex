"""Tests for canonical media alias matching."""

from assertpy import assert_that

from podex.services.media_aliases import (
    CanonicalMediaCandidate,
    MediaAliasMatchReason,
    MediaAliasSource,
    match_canonical_media,
    normalize_media_alias,
    normalize_media_title,
)


def test_matches_exact_canonical_title() -> None:
    """Verify exact title matches resolve to the canonical media candidate."""
    candidate = CanonicalMediaCandidate(
        media_id=10,
        media_type="book",
        title="The Left Hand of Darkness",
    )

    result = match_canonical_media(
        title="The Left Hand of Darkness",
        media_type="book",
        candidates=[candidate],
    )

    assert_that(result.media_id).is_equal_to(10)
    assert_that(result.candidate).is_equal_to(candidate)
    assert_that(result.confidence).is_equal_to(1.0)
    assert_that(result.reason).is_equal_to(MediaAliasMatchReason.EXACT_TITLE)
    matched_alias = result.matched_alias
    assert matched_alias is not None
    assert_that(matched_alias.source).is_equal_to(MediaAliasSource.TITLE)


def test_matches_exact_alias() -> None:
    """Verify exact alias matches resolve to the canonical media candidate."""
    candidate = CanonicalMediaCandidate(
        media_id=20,
        media_type="movie",
        title="Star Wars",
        aliases=("A New Hope", "Star Wars Episode IV"),
    )

    result = match_canonical_media(
        title="A New Hope",
        media_type="movie",
        candidates=[candidate],
    )

    assert_that(result.media_id).is_equal_to(20)
    assert_that(result.confidence).is_equal_to(0.95)
    assert_that(result.reason).is_equal_to(MediaAliasMatchReason.EXACT_ALIAS)
    matched_alias = result.matched_alias
    assert matched_alias is not None
    assert_that(matched_alias.value).is_equal_to("A New Hope")
    assert_that(matched_alias.source).is_equal_to(MediaAliasSource.ALIAS)


def test_rejects_type_mismatch_for_exact_alias_or_title() -> None:
    """Verify exact text matches do not resolve when media types differ."""
    candidate = CanonicalMediaCandidate(
        media_id=30,
        media_type="movie",
        title="Dune",
        aliases=("Dune Part One",),
    )

    result = match_canonical_media(
        title="Dune Part One",
        media_type="book",
        candidates=[candidate],
    )

    assert_that(result.media_id).is_none()
    assert_that(result.candidate).is_none()
    assert_that(result.confidence).is_equal_to(0.0)
    assert_that(result.reason).is_equal_to(MediaAliasMatchReason.TYPE_MISMATCH)


def test_normalizes_titles_and_aliases_before_matching() -> None:
    """Verify punctuation, accents, case, and spacing are normalized."""
    candidate = CanonicalMediaCandidate(
        media_id=40,
        media_type="movie",
        title="Amelie",
        aliases=("  Amelie: Le Fabuleux Destin d'Amelie Poulain  ",),
    )

    result = match_canonical_media(
        title="amelie le fabuleux destin d amelie poulain",
        media_type="movie",
        candidates=[candidate],
    )

    assert_that(normalize_media_title("  Amelie!!  ")).is_equal_to("amelie")
    assert_that(normalize_media_alias("d'Amelie")).is_equal_to("d amelie")
    assert_that(result.media_id).is_equal_to(40)
    assert_that(result.reason).is_equal_to(MediaAliasMatchReason.EXACT_ALIAS)


def test_returns_no_match_when_no_title_or_alias_matches() -> None:
    """Verify unmatched titles return an explicit no-match result."""
    candidates = [
        CanonicalMediaCandidate(
            media_id=50,
            media_type="book",
            title="Kindred",
            aliases=("Octavia Butler's Kindred",),
        )
    ]

    result = match_canonical_media(
        title="Parable of the Sower",
        media_type="book",
        candidates=candidates,
    )

    assert_that(result.media_id).is_none()
    assert_that(result.matched_alias).is_none()
    assert_that(result.confidence).is_equal_to(0.0)
    assert_that(result.reason).is_equal_to(MediaAliasMatchReason.NO_MATCH)
