"""Tests for the rule that decides whether two discovered records are one episode."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that

from podex.services.discovery import DiscoveredEpisode
from podex.services.episode_matching import episodes_match, group_same_episodes

_DAY = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
_HOMEPAGE = "https://show.example/"


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


def test_differing_numbers_from_one_source_never_match() -> None:
    """One source's differing numbers keep identical titles and shared links apart."""
    first = _episode("Protect Our Parks", number=2581, episode_url=_HOMEPAGE)
    second = _episode("Protect Our Parks", number=2580, episode_url=_HOMEPAGE)

    assert_that(episodes_match(first, second)).is_false()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rss_guid", "urn:example:5"),
        ("spotify_uri", "spotify:episode:5"),
        ("youtube_id", "abcdefghijk"),
    ],
    ids=["rss-guid", "spotify-uri", "youtube-id"],
)
def test_shared_stable_identifier_wins_over_differing_numbers(
    field: str,
    value: str,
) -> None:
    """A renumbered record with the same stable identifier is the same episode."""
    before = replace(_episode("The Return", number=5), **{field: value})
    after = replace(_episode("The Return (Remastered)", number=6), **{field: value})

    assert_that(episodes_match(before, after)).is_true()
    assert_that(group_same_episodes([before, after])).is_equal_to([[before, after]])


def test_rss_guid_identifies_records_whose_titles_differ() -> None:
    """A shared feed GUID joins records that share nothing else."""
    first = replace(_episode("A Conversation"), rss_guid="urn:example:1")
    second = replace(_episode("Completely Retitled"), rss_guid="urn:example:1")

    assert_that(episodes_match(first, second)).is_true()


def test_shared_url_ranks_below_a_differing_number_from_one_source() -> None:
    """A shared URL joins records unless one source numbers them differently."""
    unnumbered = _episode("Listing Title", episode_url=_HOMEPAGE, source="podscripts")
    other_source = _episode("Page Title", number=7, episode_url=_HOMEPAGE)
    renumbered = _episode("Page Title", number=8, episode_url=_HOMEPAGE)

    assert_that(episodes_match(unnumbered, other_source)).is_true()
    assert_that(episodes_match(other_source, renumbered)).is_false()


def test_identity_wins_over_conflicting_title_numbers() -> None:
    """A shared stable identifier or an equal number outranks the titles' numbers."""
    by_guid = (
        replace(_episode("Season Finale Part 1"), rss_guid="urn:example:9"),
        replace(_episode("Season Finale Part 2"), rss_guid="urn:example:9"),
    )
    by_number = (
        _episode("Season Finale Part 1", number=9),
        _episode("Season Finale Part 2", number=9),
    )

    assert_that(episodes_match(*by_guid)).is_true()
    assert_that(episodes_match(*by_number)).is_true()


def test_number_zero_means_no_number() -> None:
    """Records numbered 0 are not joined by that number."""
    items = [
        replace(_episode(title, number=0), rss_guid=f"urn:example:{index}")
        for index, title in enumerate(
            ["Trailer", "Bonus: Live Q&A", "Announcement", "Holiday Special"],
        )
    ]
    numbered = replace(
        _episode("Holiday Special", number=12),
        rss_guid="urn:example:12",
    )

    assert_that(episodes_match(items[0], items[1])).is_false()
    assert_that(group_same_episodes(items)).is_length(4)
    # Zero is not a differing number either: the titles decide.
    assert_that(episodes_match(items[3], numbered)).is_true()


def test_same_number_with_different_titles_is_one_episode() -> None:
    """An equal number joins records whose titles differ, a differing one never does."""
    listing = _episode("Protect Our Parks 18", number=2581, episode_url=_HOMEPAGE)
    sibling = _episode("Protect Our Parks 17", number=2580, episode_url=_HOMEPAGE)
    feed = _episode("#2581 - Protect Our Parks 18 (Live)", number=2581)

    assert_that(episodes_match(listing, feed)).is_true()
    assert_that(group_same_episodes([listing, sibling, feed])).is_equal_to(
        [[listing, feed], [sibling]],
    )


@pytest.mark.parametrize(
    ("listing_number", "feed_number", "title"),
    [
        (105, 5, "The Return"),
        (10, 347, "10 Things We Learned"),
        (2026, 347, "2026 Year In Review"),
    ],
    ids=["leading-digits-not-the-feed-number", "title-starts-with-a-number", "year"],
)
def test_differing_numbers_from_two_sources_leave_it_to_the_titles(
    listing_number: int,
    feed_number: int,
    title: str,
) -> None:
    """Across sources a differing number alone does not separate one episode."""
    listing = _episode(title, number=listing_number, source="podscripts")
    feed = _episode(title, number=feed_number, published_at=_DAY)

    assert_that(episodes_match(listing, feed)).is_true()


def test_title_numbers_still_separate_differently_numbered_sources() -> None:
    """Across sources, conflicting numbers in the titles keep a series apart."""
    listing = _episode("Protect Our Parks 18", number=2581, source="podscripts")
    feed = _episode("Protect Our Parks 17", number=17, published_at=_DAY)
    same = _episode("Protect Our Parks 18", number=18, published_at=_DAY)

    assert_that(episodes_match(listing, feed)).is_false()
    assert_that(episodes_match(listing, same)).is_true()


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
        published_at=_DAY + timedelta(days=7),
        source="spotify",
    )
    rerun = _episode(
        "Best Of The Year",
        published_at=_DAY + timedelta(days=7, minutes=1),
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


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Our 1,000th Episode Spectacular", "Our 1000th Episode Spectacular"),
        ("The $1,000,000 Question", "The $1000000 Question"),
    ],
    ids=["ordinal", "amount"],
)
def test_thousands_separators_are_not_a_number_conflict(
    first: str, second: str
) -> None:
    """A number written with and without thousands separators is one number."""
    assert_that(episodes_match(_episode(first), _episode(second))).is_true()


def test_two_digit_year_matches_its_full_year() -> None:
    """ "'25" is read as 2025 and "'99" as 1999; another year still conflicts."""
    full = _episode("Best of 2025", published_at=_DAY)
    short = _episode("Best of '25", published_at=_DAY, source="spotify")
    other = _episode("Best of '24", published_at=_DAY, source="spotify")
    nineties = _episode("Classics Of '99")
    nineties_full = _episode("Classics Of 1999", source="spotify")

    assert_that(episodes_match(full, short)).is_true()
    assert_that(episodes_match(full, other)).is_false()
    assert_that(episodes_match(nineties, nineties_full)).is_true()


def test_numbering_labels_of_different_schemes_compare_the_rest() -> None:
    """ "Episode 105: X" and "S2E5: X" from two sources on one day are one episode."""
    feed = _episode("Episode 105: The Return", published_at=_DAY)
    spotify = _episode("S2E5: The Return", published_at=_DAY, source="spotify")
    listing = _episode("#105 - The Return", source="podscripts")

    bare_feed = _episode("Episode 105", published_at=_DAY)
    bare_spotify = _episode("S2E5", published_at=_DAY, source="spotify")

    assert_that(episodes_match(feed, spotify)).is_true()
    assert_that(episodes_match(listing, spotify)).is_false()
    assert_that(episodes_match(listing, feed)).is_true()
    # With nothing after the labels, the whole titles are compared.
    assert_that(episodes_match(bare_feed, bare_spotify)).is_false()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Episode 105: The Return", "Episode 106: The Return"),
        ("S2E5: The Return", "S2E6: The Return"),
        ("#105 - The Return", "Episode 106: The Return"),
    ],
    ids=["episode-labels", "season-labels", "mixed-episode-labels"],
)
def test_numbering_labels_of_one_scheme_still_conflict(first: str, second: str) -> None:
    """Labels of the same scheme with different numbers name two episodes."""
    assert_that(
        episodes_match(
            _episode(first, published_at=_DAY),
            _episode(second, published_at=_DAY, source="spotify"),
        ),
    ).is_false()


def test_title_numbers_compare_as_multisets() -> None:
    """Repeated numbers count: "1 of 2: Game 1" and "2 of 2: Game 1" differ."""
    first = _episode("Round 1 of 2: Game 1")
    second = _episode("Round 2 of 2: Game 1", source="spotify")

    assert_that(episodes_match(first, second)).is_false()


def test_titles_compare_without_case() -> None:
    """Case alone never separates two titles."""
    first = _episode("THE RETURN OF THE KEEPER")
    second = _episode("the return of the keeper", source="spotify")

    assert_that(episodes_match(first, second)).is_true()


_BASE_TITLE = "Letters From The Lighthouse At Low Tide"


@pytest.mark.parametrize(
    ("suffix", "offset", "expected"),
    [
        (" And The Keeper Too", timedelta(hours=1), True),
        (" And The Keepers Too", timedelta(hours=1), False),
        (" Revised", timedelta(days=3), True),
        (" Part Two", timedelta(days=3), False),
        (" Two", None, True),
        (" Live", None, False),
    ],
    ids=[
        "same-day-at-80",
        "same-day-below-80",
        "near-date-at-90",
        "near-date-below-90",
        "undated-at-95",
        "undated-below-95",
    ],
)
def test_title_similarity_thresholds(
    suffix: str,
    offset: timedelta | None,
    expected: bool,
) -> None:
    """Same day needs 80, within the week 90, undated 95 title similarity."""
    first = _episode(_BASE_TITLE, published_at=_DAY if offset is not None else None)
    second = _episode(
        _BASE_TITLE + suffix,
        published_at=_DAY + offset if offset is not None else None,
        source="spotify",
    )

    assert_that(episodes_match(first, second)).is_equal_to(expected)
