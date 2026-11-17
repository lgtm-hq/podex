"""Tests for the rule that decides whether two discovered records are one episode."""

from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that

from podex.services.discovery import DiscoveredEpisode
from podex.services.episode_matching import episodes_match, group_same_episodes

_DAY = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _episode(
    title: str,
    *,
    number: int | None = None,
    published_at: datetime | None = None,
    episode_url: str | None = None,
    source: str = "rss",
) -> DiscoveredEpisode:
    """Return a discovered episode with only the fields under test."""
    return DiscoveredEpisode(
        title=title,
        episode_number=number,
        published_at=published_at,
        episode_url=episode_url,
        discovery_source=source,
    )


@pytest.mark.parametrize(
    "published_at",
    [None, _DAY],
    ids=["undated", "same-day"],
)
def test_numbered_series_titles_name_different_episodes(
    published_at: datetime | None,
) -> None:
    """Titles that differ only by their series number are two episodes."""
    eighteen = _episode("Protect Our Parks 18", published_at=published_at)
    seventeen = _episode("Protect Our Parks 17", published_at=published_at)

    assert_that(episodes_match(eighteen, seventeen)).is_false()


def test_differing_numbers_never_match() -> None:
    """Known numbers that differ keep identical titles and shared links apart."""
    homepage = "https://show.example/"
    first = _episode("Protect Our Parks", number=2581, episode_url=homepage)
    second = _episode("Protect Our Parks", number=2580, episode_url=homepage)

    assert_that(episodes_match(first, second)).is_false()


def test_same_number_with_different_titles_is_one_episode() -> None:
    """An equal number joins records whose titles differ, a differing one never does."""
    homepage = "https://show.example/"
    listing = _episode(
        "Protect Our Parks 18",
        number=2581,
        episode_url=homepage,
        source="podscripts",
    )
    sibling = _episode("Protect Our Parks 17", number=2580, episode_url=homepage)
    feed = _episode("#2581 - Protect Our Parks 18 (Live)", number=2581)

    assert_that(episodes_match(listing, feed)).is_true()
    assert_that(group_same_episodes([listing, sibling, feed])).is_equal_to(
        [[listing, feed], [sibling]],
    )


def test_cross_source_duplicate_matches_only_its_own_episode() -> None:
    """A slightly different title from another source joins its own episode."""
    feed_eighteen = _episode("Protect Our Parks 18", number=2581, published_at=_DAY)
    feed_seventeen = _episode("Protect Our Parks 17", number=2580, published_at=_DAY)
    spotify_eighteen = _episode(
        "Protect Our Parks #18",
        published_at=_DAY + timedelta(hours=3),
        source="spotify",
    )

    assert_that(episodes_match(feed_eighteen, spotify_eighteen)).is_true()
    assert_that(episodes_match(spotify_eighteen, feed_seventeen)).is_false()


def test_conflicting_dates_keep_similar_titles_apart() -> None:
    """Identical titles published more than a week apart are two episodes."""
    original = _episode("Best Of The Year", published_at=_DAY)
    nearby = _episode(
        "Best Of The Year",
        published_at=_DAY + timedelta(days=3),
        source="spotify",
    )
    rerun = _episode(
        "Best Of The Year",
        published_at=_DAY + timedelta(days=60),
        source="spotify",
    )

    assert_that(episodes_match(original, nearby)).is_true()
    assert_that(episodes_match(original, rerun)).is_false()


def test_number_known_on_one_side_still_compares_title_numbers() -> None:
    """With a number on one side only, the titles' numbers decide."""
    numbered = _episode("Protect Our Parks 18", number=2581, source="podscripts")
    same = _episode("Protect Our Parks #18")
    sibling = _episode("Protect Our Parks 17")

    assert_that(episodes_match(numbered, same)).is_true()
    assert_that(episodes_match(numbered, sibling)).is_false()


def test_siblings_never_merge_through_a_shared_match() -> None:
    """Two numbered siblings stay apart when both resemble a third record."""
    untitled = _episode("Protect Our Parks", published_at=_DAY, source="spotify")
    eighteen = _episode("Protect Our Parks 18", number=2581, published_at=_DAY)
    seventeen = _episode("Protect Our Parks 17", number=2580, published_at=_DAY)

    groups = group_same_episodes([untitled, eighteen, seventeen])

    assert_that(groups).is_equal_to([[untitled, eighteen], [seventeen]])
