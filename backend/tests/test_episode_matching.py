"""Tests for the rule that decides whether two discovered records are one episode."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that

from podex.services.discovery import DiscoveredEpisode
from podex.services.episode_matching import (
    episodes_match,
    group_same_episodes,
    guid_key,
    title_numbers_conflict,
)

_DAY = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
_HOMEPAGE = "https://show.example/"


def _episode(
    title: str,
    *,
    number: int | None = None,
    published_at: datetime | None = None,
    episode_url: str | None = None,
    source: str | None = "rss",
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


def test_records_without_a_source_are_never_the_same_source() -> None:
    """Without a source, a differing number does not separate; the titles decide."""
    first = _episode("Protect Our Parks", number=5, source=None)
    second = _episode("Protect Our Parks", number=6, source=None)
    sourced = _episode("Protect Our Parks", number=6)

    assert_that(episodes_match(first, second)).is_true()
    assert_that(episodes_match(first, sourced)).is_true()


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


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_blank_identifiers_identify_nothing(blank: str) -> None:
    """An empty or blank GUID shared by two records does not join them."""
    first = replace(_episode("A Conversation"), rss_guid=blank)
    second = replace(_episode("Completely Retitled"), rss_guid=blank)

    assert_that(episodes_match(first, second)).is_false()


def test_stable_identifier_outranks_numbers_when_grouping() -> None:
    """Records sharing a GUID stay together and take the third record's number too.

    From one source, numbers 5 and 6 would separate the first two records;
    the shared GUID outranks that, and the listing's 5 then joins them.
    """
    before = replace(_episode("The Return", number=5), rss_guid="urn:example:g")
    after = replace(_episode("The Return", number=6), rss_guid="urn:example:g")
    listing = _episode("The Return", number=5, source="podscripts")

    assert_that(group_same_episodes([before, after, listing])).is_equal_to(
        [[before, after, listing]],
    )


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
    assert_that(group_same_episodes([unnumbered, other_source])).is_equal_to(
        [[unnumbered, other_source]],
    )


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
    """Records numbered 0 are not joined by that number.

    The items come from one feed and carry no GUIDs, so only the numbers and
    the titles can decide: an equal number 0 would join them, and a differing
    number from one source would keep "Holiday Special" 0 and 12 apart.
    """
    items = [
        _episode(title, number=0)
        for title in ["Trailer", "Bonus: Live Q&A", "Announcement", "Holiday Special"]
    ]
    numbered = _episode("Holiday Special", number=12)

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


@pytest.mark.parametrize(
    "newest_first", [True, False], ids=["18-then-17", "17-then-18"]
)
def test_equal_number_with_matching_titles_pairs_first(newest_first: bool) -> None:
    """An equal number never loses to an earlier match on identical titles."""
    listing = _episode("Protect Our Parks", number=17, source="podscripts")
    seventeen = _episode("Protect Our Parks", number=17, published_at=_DAY)
    eighteen = _episode(
        "Protect Our Parks",
        number=18,
        published_at=_DAY + timedelta(days=7),
    )
    feed = [eighteen, seventeen] if newest_first else [seventeen, eighteen]

    groups = group_same_episodes([listing, *feed])

    assert_that(groups).contains([listing, seventeen], [eighteen])
    assert_that(groups).is_length(2)


@pytest.mark.parametrize("tenth_first", [True, False], ids=["tenth-first", "ten-first"])
def test_title_matches_pair_before_an_equal_number_alone(tenth_first: bool) -> None:
    """An equal number whose titles disagree yields to two title matches."""
    tenth = _episode("The Tenth One", source="podscripts")
    things = _episode("10 Things We Learned", number=10, source="podscripts")
    feed_tenth = _episode("The Tenth One", number=10, published_at=_DAY)
    feed_things = _episode("10 Things We Learned", number=347, published_at=_DAY)
    listing = [tenth, things] if tenth_first else [things, tenth]

    groups = group_same_episodes([*listing, feed_tenth, feed_things])

    assert_that(groups).contains([tenth, feed_tenth], [things, feed_things])
    assert_that(groups).is_length(2)


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


def test_title_numbers_veto_a_group_through_a_shared_match() -> None:
    """Unnumbered siblings whose titles' numbers conflict never meet via a third."""
    untitled = _episode("Protect Our Parks", published_at=_DAY, source="spotify")
    eighteen = _episode("Protect Our Parks 18", published_at=_DAY)
    seventeen = _episode("Protect Our Parks 17", published_at=_DAY)

    groups = group_same_episodes([untitled, eighteen, seventeen])

    assert_that(groups).is_equal_to([[untitled, eighteen], [seventeen]])


@pytest.mark.parametrize(
    ("one", "other", "conflict"),
    [
        ("Our 1,000th Episode Spectacular", "Our 1000th Episode Spectacular", False),
        ("The $1,000,000 Question", "The $1000000 Question", False),
        ("Episodes 100,101", "Episodes 100 and 101", False),
        ("Episodes 100,101", "Episode 102", True),
        ("Best of the '90s", "Best of the 90s", False),
        ("Class of '99", "Class of 99", False),
        ("The '10 Things' Episode", "The 10 Things Episode", False),
        ("Best of 2025", "Best of '25", False),
        ("Best of 2025", "Best of '24", True),
        ("Class of '68", "Class of 2068", False),
        ("Class of '68", "Class of 1968", True),
        ("Class of '69", "Class of 1969", False),
        ("Class of '69", "Class of 2069", True),
        ("Round 1 of 2: Game 1", "Round 2 of 2: Game 1", True),
    ],
    ids=[
        "thousands-ordinal",
        "thousands-amount",
        "comma-list",
        "comma-list-other-number",
        "decade",
        "short-year-bare",
        "quoted-number",
        "short-year-full",
        "short-year-other",
        "pivot-68-is-2068",
        "pivot-68-not-1968",
        "pivot-69-is-1969",
        "pivot-69-not-2069",
        "repeats-count",
    ],
)
def test_title_numbers_conflict(one: str, other: str, conflict: bool) -> None:
    """Separators, short years and repeated numbers read as people write them."""
    assert_that(title_numbers_conflict(one, other)).is_equal_to(conflict)
    assert_that(title_numbers_conflict(other, one)).is_equal_to(conflict)


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


def test_short_years_merge_on_the_same_day() -> None:
    """ "'25" and "2025", "'90s" and "90s" match as they did before."""
    full = _episode("Best of 2025", published_at=_DAY)
    short = _episode("Best of '25", published_at=_DAY, source="spotify")
    other = _episode("Best of '24", published_at=_DAY, source="spotify")
    decade = _episode("Best of the '90s")
    bare_decade = _episode("Best of the 90s", source="spotify")

    assert_that(episodes_match(full, short)).is_true()
    assert_that(episodes_match(full, other)).is_false()
    assert_that(episodes_match(decade, bare_decade)).is_true()


def test_similarity_uses_the_titles_as_written() -> None:
    """Normalised numbers never make dissimilar titles similar."""
    short = _episode("Top 10 of '24", published_at=_DAY)
    longer = _episode("Top 10 of 2024 part 2", published_at=_DAY, source="spotify")

    assert_that(episodes_match(short, longer)).is_false()


def test_numbering_labels_of_different_schemes_compare_the_rest() -> None:
    """ "Episode 105: X" and "S2E5: X" are one episode only across sources, same day."""
    feed = _episode("Episode 105: The Return", published_at=_DAY)
    spotify = _episode("S2E5: The Return", published_at=_DAY, source="spotify")
    same_source = _episode("S2E5: The Return", published_at=_DAY)
    next_day = _episode(
        "S2E5: The Return",
        published_at=_DAY + timedelta(days=1),
        source="spotify",
    )
    bare_feed = _episode("Episode 105", published_at=_DAY)
    bare_spotify = _episode("S2E5", published_at=_DAY, source="spotify")

    assert_that(episodes_match(feed, spotify)).is_true()
    assert_that(episodes_match(feed, same_source)).is_false()
    assert_that(episodes_match(feed, next_day)).is_false()
    assert_that(
        episodes_match(
            _episode("Episode 105: The Return"),
            _episode("S2E5: The Return", source="spotify"),
        ),
    ).is_false()
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


@pytest.mark.parametrize(
    ("guid", "key"),
    [
        ("urn:Show:1", "urn:show:1"),
        ("  URN:SHOW:1\t", "urn:show:1"),
        ("urn:show:X\u00c9", "urn:show:x\u00c9"),
        ("   ", None),
        ("", None),
        (None, None),
    ],
    ids=[
        "ascii-case",
        "surrounding-whitespace",
        "non-ascii-exact",
        "blank",
        "empty",
        "none",
    ],
)
def test_guid_key(guid: str | None, key: str | None) -> None:
    """GUIDs compare without surrounding whitespace or ASCII letter case."""
    assert_that(guid_key(guid)).is_equal_to(key)


def test_guids_differing_only_in_case_and_spacing_are_one_episode() -> None:
    """A GUID written in another case or with spaces still identifies the episode."""
    first = replace(_episode("The Return", number=5), rss_guid="URN:UUID:AB")
    second = replace(_episode("Retitled", number=6), rss_guid=" urn:uuid:ab ")

    assert_that(episodes_match(first, second)).is_true()
    assert_that(group_same_episodes([first, second])).is_length(1)


def test_distinct_guids_of_one_feed_never_match_by_title() -> None:
    """Identically titled items a day apart with their own GUIDs stay apart.

    A listing record without a date pairs with one of them only.
    """
    briefings = [
        replace(
            _episode("Morning Briefing", published_at=_DAY + timedelta(days=day)),
            rss_guid=f"urn:show:briefing:{day}",
        )
        for day in range(4)
    ]
    listing = _episode("Morning Briefing", source="podscripts")

    assert_that(episodes_match(briefings[0], briefings[1])).is_false()
    groups = group_same_episodes([listing, *briefings])
    assert_that(groups).is_length(4)
    assert_that(groups[0]).is_equal_to([listing, briefings[0]])


def test_distinct_guids_outrank_an_equal_number_and_a_shared_url() -> None:
    """Two items of one feed with their own GUIDs differ whatever else they share."""
    first = replace(
        _episode("The Return", number=5, episode_url=_HOMEPAGE),
        rss_guid="urn:show:a",
    )
    second = replace(
        _episode("The Return", number=5, episode_url=_HOMEPAGE),
        rss_guid="urn:show:b",
    )

    assert_that(episodes_match(first, second)).is_false()


def test_distinct_guids_from_records_without_a_source_leave_it_to_the_titles() -> None:
    """Records without a source are never the same feed; the titles decide."""
    first = replace(_episode("Morning Briefing", source=None), rss_guid="urn:a")
    second = replace(_episode("Morning Briefing", source=None), rss_guid="urn:b")

    assert_that(episodes_match(first, second)).is_true()


def _seasoned(
    title: str,
    season: int | None,
    number: int,
    *,
    source: str | None = "rss",
) -> DiscoveredEpisode:
    """Return a record numbered within ``season``."""
    return replace(_episode(title, number=number, source=source), season_number=season)


def test_equal_numbers_in_two_seasons_of_one_feed_differ() -> None:
    """Season 1 episode 5 and season 2 episode 5 of one feed are two episodes."""
    first = _seasoned("Opening Night", 1, 5)
    second = _seasoned("Opening Night", 2, 5)

    assert_that(episodes_match(first, second)).is_false()
    assert_that(group_same_episodes([first, second])).is_length(2)


@pytest.mark.parametrize(
    ("first_season", "second_season"),
    [(None, 2), (2, None), (2, 2), (0, 2)],
    ids=["first-unknown", "second-unknown", "same-season", "season-zero-is-none"],
)
def test_an_unknown_season_leaves_the_numbers_comparable(
    first_season: int | None,
    second_season: int | None,
) -> None:
    """Equal numbers still mean one episode unless both seasons are known and differ."""
    first = _seasoned("The Return", first_season, 5, source="podscripts")
    second = _seasoned("Something Else", second_season, 5)

    assert_that(episodes_match(first, second)).is_true()


def test_equal_numbers_in_two_seasons_from_two_sources_leave_it_to_the_titles() -> None:
    """Across sources, differing seasons decide nothing; the titles do."""
    same_title = (
        _seasoned("The Return", 1, 5, source="spotify"),
        _seasoned("The Return", 2, 5),
    )
    other_title = (
        _seasoned("The Return", 1, 5, source="spotify"),
        _seasoned("A Different Story", 2, 5),
    )

    assert_that(episodes_match(*same_title)).is_true()
    assert_that(episodes_match(*other_title)).is_false()


def test_grouping_tiers_respect_seasons() -> None:
    """Equal numbers in two seasons give no number tier, even across sources.

    The titles differ, so only an equal-number tier could join the records.
    """
    first = _seasoned("The Arrival", 1, 5, source="spotify")
    second = _seasoned("The Departure", 2, 5)

    assert_that(group_same_episodes([first, second])).is_equal_to(
        [[first], [second]],
    )


def test_a_listing_number_pairs_with_a_seasoned_feed_number_as_before() -> None:
    """The listing has no season, so an equal number pairs as it did without seasons."""
    listing = _episode("Opening Night", number=5, source="podscripts")
    first = _seasoned("Opening Night", 1, 5)
    second = _seasoned("Opening Night", 2, 5)

    groups = group_same_episodes([listing, first, second])

    assert_that(groups).is_equal_to([[listing, first], [second]])
