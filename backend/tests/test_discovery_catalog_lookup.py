"""How discovery finds the stored row for a feed item across runs (ADR 0020).

Each test runs the real orchestrator over synthetic feeds and listings and
observes the stored rows; nothing is fetched from the network.
"""

from datetime import timedelta

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast
from podex.services.discovery import DiscoveredEpisode
from tests.feed_identity_support import (
    DAY,
    MARK,
    SITE,
    FeedItem,
    Round,
    counts,
    discover,
    marked_record,
    show,
    store,
    stored,
    two_seasons,
)

_record = marked_record


def _probe(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    record: DiscoveredEpisode,
) -> Episode | None:
    """Discover ``record`` alone; return the row it updated, or None if it added one."""
    before = len(stored(db, podcast))
    after = discover(
        db,
        monkeypatch,
        podcast,
        Round(records=[record]),
    )
    if after > before:
        return None
    updated = [row for row in stored(db, podcast) if row.duration_seconds == MARK]
    assert_that(updated).is_length(1)
    return updated[0]


# --- Items left without an identifier (B1) ---------------------------------

_HOMEPAGE_NO_GUIDS = [
    FeedItem(title, link=SITE, published_at=None)
    for title in ("Why We Started", "Listener Mail", "A Long Goodbye")
]
_ONE_GUID_HOMEPAGE = [
    FeedItem(title, guid="urn:show:feed", link=SITE, published_at=None)
    for title in ("Why We Started", "Listener Mail")
]
_UNREADABLE_DATES = [
    FeedItem(title, link=SITE, published_at="2026-05-04T06:00:00Z")
    for title in ("Why We Started", "Listener Mail", "A Long Goodbye")
]
_UNDATED_FEEDS = pytest.mark.parametrize(
    ("items", "main_row"),
    [
        (_HOMEPAGE_NO_GUIDS, {"episode_url": SITE}),
        (_ONE_GUID_HOMEPAGE, {"episode_url": SITE, "rss_guid": "urn:show:feed"}),
        (_UNREADABLE_DATES, {"episode_url": SITE}),
    ],
    ids=["homepage-links-no-guids", "one-guid-homepage-links", "unreadable-dates"],
)


@_UNDATED_FEEDS
def test_undated_items_without_identifiers_are_stored_once(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    items: list[FeedItem],
    main_row: dict[str, object],
) -> None:
    """Undated items whose link and GUID name the show keep one row each."""
    del main_row
    podcast = show(db_session)

    row_counts = counts(db_session, monkeypatch, podcast, [Round(items=items)] * 4)

    assert_that(row_counts).is_equal_to([len(items)] * 4)


@_UNDATED_FEEDS
def test_undated_items_find_the_row_an_earlier_release_merged_them_into(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    items: list[FeedItem],
    main_row: dict[str, object],
) -> None:
    """The row the earlier release stored for the whole feed keeps its first item.

    That release merged every item into one row with the first item's title,
    the shared link and the shared GUID, and no date.
    """
    podcast = show(db_session)
    merged = store(db_session, podcast, title=items[0].title, **main_row)

    row_counts = counts(db_session, monkeypatch, podcast, [Round(items=items)] * 4)

    assert_that(row_counts).is_equal_to([len(items)] * 4)
    assert_that(stored(db_session, podcast)[0].id).is_equal_to(merged.id)


def test_several_undated_rows_with_one_title_resolve_to_the_oldest(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An undated record without identifiers updates the lowest-id row of its title."""
    podcast = show(db_session)
    first = store(db_session, podcast, title="Mailbag")
    store(db_session, podcast, title="Mailbag")

    found = _probe(db_session, monkeypatch, podcast, _record("Mailbag"))

    assert_that(found and found.id).is_equal_to(first.id)


# --- The title and time step and what it never takes ------------------------


@pytest.mark.parametrize(
    ("record", "found"),
    [
        (_record("Why We Started", published_at=DAY), True),
        (_record("Why We Started", published_at=DAY.replace(tzinfo=None)), True),
        (_record("Why We Started", published_at=DAY + timedelta(minutes=1)), False),
        (_record("Why We Started"), False),
        (_record("Why we started", published_at=DAY), False),
        (_record("Why We Started", published_at=DAY, rss_guid="urn:show:a"), True),
        (_record("Why We Started", published_at=DAY, episode_url=f"{SITE}a"), True),
        (_record("Why We Started", published_at=DAY, episode_number=3), True),
        (
            _record(
                "Why We Started",
                published_at=DAY,
                spotify_uri="spotify:episode:a",
            ),
            False,
        ),
    ],
    ids=[
        "same-title-and-time",
        "naive-time-read-as-utc",
        "other-time",
        "undated-record-dated-row",
        "other-title",
        "record-guid-row-without",
        "record-url-row-without",
        "record-number-row-without",
        "spotify-record",
    ],
)
def test_title_and_time_find_a_row_without_conflicting_evidence(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    record: DiscoveredEpisode,
    found: bool,
) -> None:
    """A record nothing else found takes the row of its title and time."""
    podcast = show(db_session)
    row = store(db_session, podcast, title="Why We Started", published_at=DAY)

    hit = _probe(db_session, monkeypatch, podcast, record)

    assert_that(hit and hit.id).is_equal_to(row.id if found else None)


@pytest.mark.parametrize(
    ("row_fields", "record_fields"),
    [
        ({"rss_guid": "urn:show:a"}, {"rss_guid": "urn:show:b"}),
        ({"episode_url": f"{SITE}a"}, {"episode_url": f"{SITE}b"}),
        ({"episode_number": 3}, {"episode_number": 4}),
        (
            {"episode_number": 3, "season_number": 1},
            {"episode_number": 3, "season_number": 2},
        ),
    ],
    ids=["other-guid", "other-url", "other-number", "other-season"],
)
@pytest.mark.parametrize("dated", [True, False], ids=["dated", "undated"])
def test_title_and_time_never_take_a_row_with_conflicting_evidence(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    row_fields: dict[str, object],
    record_fields: dict[str, object],
    dated: bool,
) -> None:
    """Two "Mailbag" listing entries or feed items with their own values stay apart."""
    podcast = show(db_session)
    published_at = DAY if dated else None
    store(
        db_session,
        podcast,
        title="Mailbag",
        published_at=published_at,
        **row_fields,
    )

    hit = _probe(
        db_session,
        monkeypatch,
        podcast,
        _record("Mailbag", published_at=published_at, **record_fields),
    )

    assert_that(hit).is_none()


# --- A link that stops being shared (N2) --------------------------------------

_SHARED = f"{SITE}listen"


def _episodes(**links: str) -> list[FeedItem]:
    """Return three dated items without GUIDs or numbers, with these links."""
    days = {"first": 0, "second": 3, "third": 9}
    return [
        FeedItem(
            name.title(),
            link=links[name],
            published_at=DAY + timedelta(days=offset),
        )
        for name, offset in days.items()
        if name in links
    ]


def test_a_link_that_stops_being_shared_keeps_one_row_per_item(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two items shared a link by mistake; once fixed, each row gains its own link."""
    podcast = show(db_session)
    shared = _episodes(first=_SHARED, second=_SHARED, third=f"{SITE}3")
    fixed = _episodes(first=f"{SITE}1", second=f"{SITE}2", third=f"{SITE}3")

    row_counts = counts(
        db_session,
        monkeypatch,
        podcast,
        [Round(items=shared)] * 2 + [Round(items=fixed)] * 2,
    )

    assert_that(row_counts).is_equal_to([3, 3, 3, 3])
    assert_that([row.episode_url for row in stored(db_session, podcast)]).is_equal_to(
        [f"{SITE}1", f"{SITE}2", f"{SITE}3"],
    )


def test_a_link_left_alone_when_its_sharer_slides_out_keeps_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feed window drops the first item, so the second item's link is unique."""
    podcast = show(db_session)
    runs = [
        Round(items=_episodes(first=_SHARED, second=_SHARED, third=f"{SITE}3")),
        Round(items=_episodes(second=_SHARED, third=f"{SITE}3")),
        Round(items=_episodes(second=_SHARED, third=f"{SITE}3")),
    ]

    row_counts = counts(db_session, monkeypatch, podcast, runs)

    assert_that(row_counts).is_equal_to([3, 3, 3])
    assert_that([row.episode_url for row in stored(db_session, podcast)]).is_equal_to(
        [None, _SHARED, f"{SITE}3"],
    )


# --- Seasons on rows stored before the season column (N1, N6) -----------------


def _season_one(guid: str, link: str) -> FeedItem:
    """Return season 1 episode 5."""
    return FeedItem("The Arrival", guid=guid, link=link, season="1", episode="5")


def test_a_guid_change_after_a_run_that_filled_the_season_keeps_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run reporting the old GUID fills the season; the new GUID then finds it."""
    podcast = show(db_session)
    store(
        db_session,
        podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid="urn:show:old",
        episode_url=f"{SITE}old",
    )
    runs = [
        Round(items=[_season_one("urn:show:old", f"{SITE}old")]),
        Round(items=[_season_one("urn:show:new", f"{SITE}new")]),
        Round(items=[_season_one("urn:show:new", f"{SITE}new")]),
    ]

    row_counts = counts(db_session, monkeypatch, podcast, runs)

    assert_that(row_counts).is_equal_to([1, 1, 1])
    assert_that(stored(db_session, podcast)[0].season_number).is_equal_to(1)


def test_a_guid_change_on_a_fresh_catalog_keeps_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row stored with its season is found by number when GUID and link change."""
    podcast = show(db_session)
    runs = [
        Round(items=[_season_one("urn:show:old", f"{SITE}old")]),
        Round(items=[_season_one("urn:show:new", f"{SITE}new")]),
    ]

    assert_that(counts(db_session, monkeypatch, podcast, runs)).is_equal_to([1, 1])


def test_a_guid_change_before_any_run_fills_the_season_adds_a_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not covered (ADR 0020): a season-less row with another GUID is not taken."""
    podcast = show(db_session)
    store(
        db_session,
        podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid="urn:show:old",
        episode_url=f"{SITE}old",
    )

    row_counts = counts(
        db_session,
        monkeypatch,
        podcast,
        [Round(items=[_season_one("urn:show:new", f"{SITE}new")])] * 2,
    )

    assert_that(row_counts).is_equal_to([2, 2])


@pytest.mark.parametrize(
    ("row_fields", "record_fields", "season"),
    [
        (
            {"episode_number": 5, "rss_guid": "urn:show:a"},
            {"episode_number": 5, "season_number": 2, "rss_guid": "urn:show:a"},
            2,
        ),
        (
            {"episode_number": 5, "episode_url": f"{SITE}a"},
            {"episode_number": 5, "season_number": 2, "episode_url": f"{SITE}a"},
            2,
        ),
        (
            {"episode_number": 7, "rss_guid": "urn:show:a"},
            {"episode_number": 5, "season_number": 2, "rss_guid": "urn:show:a"},
            None,
        ),
        (
            {"episode_number": 5, "season_number": 1, "rss_guid": "urn:show:a"},
            {"episode_number": 5, "season_number": 2, "rss_guid": "urn:show:a"},
            1,
        ),
        (
            {"episode_number": 5, "title": "Unrelated", "published_at": DAY},
            {"episode_number": 5, "season_number": 2, "published_at": DAY},
            None,
        ),
    ],
    ids=[
        "filled-by-guid",
        "filled-by-url",
        "numbers-differ",
        "never-overwritten",
        "not-by-number-or-title",
    ],
)
def test_a_matched_row_gains_a_missing_season_only_by_guid_or_url(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    row_fields: dict[str, object],
    record_fields: dict[str, object],
    season: int | None,
) -> None:
    """The season is filled with an agreeing number and never overwritten."""
    podcast = show(db_session)
    fields = {"title": "Stored", **row_fields}
    row = store(db_session, podcast, **fields)

    hit = _probe(db_session, monkeypatch, podcast, _record(**record_fields))

    assert_that(hit and hit.id).is_equal_to(row.id)
    db_session.refresh(row)
    assert_that(row.season_number).is_equal_to(season)


@pytest.mark.parametrize(
    ("stored_seasons", "record_season", "expected"),
    [
        ((1, None), 2, 1),
        ((None, 2), 2, 1),
        ((1,), 2, None),
        ((1, 2), None, 0),
    ],
    ids=[
        "seasonless-row-when-no-row-of-the-season",
        "row-of-the-season-before-a-seasonless-row",
        "never-a-row-of-another-season",
        "record-without-season-matches-any-season",
    ],
)
def test_number_lookup_respects_the_season(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stored_seasons: tuple[int | None, ...],
    record_season: int | None,
    expected: int | None,
) -> None:
    """Number 5 finds a row of the record's season, then one without a season."""
    podcast = show(db_session)
    rows = [
        store(
            db_session,
            podcast,
            title=f"Stored {index}",
            episode_number=5,
            season_number=season,
        )
        for index, season in enumerate(stored_seasons)
    ]

    hit = _probe(
        db_session,
        monkeypatch,
        podcast,
        _record(episode_number=5, season_number=record_season),
    )

    assert_that(hit and hit.id).is_equal_to(
        None if expected is None else rows[expected].id,
    )


@pytest.mark.parametrize(
    ("stored_guid", "record_guid", "found"),
    [
        ("urn:show:s1e5", "urn:show:s2e5", False),
        (None, "urn:show:s2e5", True),
        ("urn:show:s1e5", None, True),
    ],
    ids=[
        "row-with-another-guid-is-another-item",
        "row-without-a-guid",
        "record-without-a-guid",
    ],
)
def test_a_seasoned_record_takes_a_seasonless_row_only_without_another_guid(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stored_guid: str | None,
    record_guid: str | None,
    found: bool,
) -> None:
    """A row stored before seasons were is not another feed item's by number."""
    podcast = show(db_session)
    row = store(
        db_session,
        podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid=stored_guid,
    )

    hit = _probe(
        db_session,
        monkeypatch,
        podcast,
        _record(
            "The Departure",
            episode_number=5,
            season_number=2,
            rss_guid=record_guid,
        ),
    )

    assert_that(hit and hit.id).is_equal_to(row.id if found else None)


def test_season_two_is_stored_beside_a_row_stored_before_seasons(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Season 2 episode 5 gets its own row next to a season-less episode 5 row."""
    podcast = show(db_session)
    store(
        db_session,
        podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid="urn:show:s1e5",
        episode_url=f"{SITE}s1e5",
        published_at=DAY - timedelta(days=300),
    )
    items = two_seasons()

    row_counts = counts(db_session, monkeypatch, podcast, [Round(items=items)] * 2)

    assert_that(row_counts).is_equal_to([2, 2])
    assert_that(
        [(row.title, row.season_number) for row in stored(db_session, podcast)],
    ).is_equal_to([("The Arrival", 1), ("The Departure", 2)])


# --- GUID comparison in the lookup (N4, N5) -----------------------------------


@pytest.mark.parametrize(
    ("stored_guid", "reported", "found"),
    [
        ("  URN:Show:Return\t", "urn:show:return", True),
        ("urn:show:return", " URN:SHOW:RETURN ", True),
        ("urn:show:É", "urn:show:é", False),
        ("urn:show:é", "URN:SHOW:é", True),
        ("urn:show:50%_off", "URN:SHOW:50%_OFF", True),
        ("urn:show:50xyoff", "urn:show:50%_off", False),
        ("urn:show:10", "urn:show:1", False),
        ("   ", "   ", False),
    ],
    ids=[
        "whitespace-and-case",
        "case",
        "non-ascii-case-is-exact",
        "ascii-case-around-non-ascii",
        "like-characters-literal",
        "like-characters-not-wildcards",
        "prefix-is-not-equal",
        "blank-identifies-nothing",
    ],
)
def test_the_lookup_compares_guids_by_the_rule(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stored_guid: str,
    reported: str,
    found: bool,
) -> None:
    """A stored GUID is found by surrounding whitespace and ASCII case only."""
    podcast = show(db_session)
    row = store(db_session, podcast, title="Stored", rss_guid=stored_guid)

    hit = _probe(db_session, monkeypatch, podcast, _record(rss_guid=reported))

    assert_that(hit and hit.id).is_equal_to(row.id if found else None)


@pytest.mark.parametrize(
    ("stored_guids", "reported", "expected"),
    [
        (("URN:X", "urn:x"), "urn:x", 1),
        (("Urn:X", "URN:X", "urn:X"), "urn:x", 0),
    ],
    ids=["exact-guid-first", "otherwise-the-lowest-id"],
)
def test_guids_differing_only_in_case_resolve_in_a_fixed_order(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stored_guids: tuple[str, ...],
    reported: str,
    expected: int,
) -> None:
    """The GUID as written wins; among case variants the oldest row wins."""
    podcast = show(db_session)
    rows = [
        store(db_session, podcast, title=f"Stored {index}", rss_guid=guid)
        for index, guid in enumerate(stored_guids)
    ]

    hit = _probe(db_session, monkeypatch, podcast, _record(rss_guid=reported))

    assert_that(hit and hit.id).is_equal_to(rows[expected].id)


# --- Known gaps, pinned (ADR 0020, not covered) -------------------------------


def test_items_numbered_alike_with_their_own_guids_share_a_row_in_the_lookup(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not covered: a later item 5 with its own GUID finds the stored item 5.

    The matcher keeps the two apart within one run, but the lookup still
    matches the stored row by number, as on main (1, 1, 1 rows there too).
    """
    podcast = show(db_session)
    part_a = FeedItem(
        "Part A",
        guid="urn:show:a",
        link=f"{SITE}a",
        episode="5",
        published_at=DAY - timedelta(days=1),
    )
    part_b = FeedItem("Part B", guid="urn:show:b", link=f"{SITE}b", episode="5")
    runs = [Round(items=[part_a])] + [Round(items=[part_b, part_a])] * 2

    assert_that(counts(db_session, monkeypatch, podcast, runs)).is_equal_to([1, 1, 1])


def test_an_unseasoned_bonus_numbered_like_two_seasoned_items_shares_a_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not covered: S1E5, an unseasoned bonus 5 and S2E5 end as two rows.

    The bonus finds season 1 episode 5 by number; main stores one row.
    """
    podcast = show(db_session)
    arrival = FeedItem(
        "The Arrival",
        guid="urn:show:s1e5",
        link=f"{SITE}1",
        season="1",
        episode="5",
        published_at=DAY - timedelta(days=300),
    )
    bonus = FeedItem(
        "Bonus",
        guid="urn:show:bonus",
        link=f"{SITE}b",
        episode="5",
        published_at=DAY - timedelta(days=100),
    )
    departure = FeedItem(
        "The Departure",
        guid="urn:show:s2e5",
        link=f"{SITE}2",
        season="2",
        episode="5",
    )
    runs = [
        Round(items=[arrival]),
        Round(items=[bonus, arrival]),
        Round(items=[departure, bonus, arrival]),
        Round(items=[departure, bonus, arrival]),
    ]

    assert_that(counts(db_session, monkeypatch, podcast, runs)).is_equal_to(
        [1, 1, 2, 2],
    )


_ACROSS_THE_SHOW = {"17-second-wind": "Second Wind", "5-opening-night": "Opening Night"}
_PER_SEASON_NEWEST_FIRST = [
    FeedItem(
        "Second Wind",
        guid="urn:show:s2e5",
        link=f"{SITE}s2e5",
        season="2",
        episode="5",
    ),
    FeedItem(
        "Opening Night",
        guid="urn:show:s1e5",
        link=f"{SITE}s1e5",
        season="1",
        episode="5",
        published_at=DAY - timedelta(days=300),
    ),
]
_LISTING = Round(links=_ACROSS_THE_SHOW)
_FEED = Round(items=_PER_SEASON_NEWEST_FIRST)
_BOTH = Round(links=_ACROSS_THE_SHOW, items=_PER_SEASON_NEWEST_FIRST)


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([_LISTING] + [_FEED] * 3, [2, 3, 3, 3]),
        ([_FEED] + [_LISTING] * 2, [2, 3, 3]),
        ([_LISTING] + [_BOTH] * 3, [2, 2, 2, 2]),
        ([_FEED] + [_BOTH] * 2, [2, 2, 2]),
    ],
    ids=[
        "listing-then-feed-alone",
        "feed-then-listing-alone",
        "listing-then-both",
        "feed-then-both",
    ],
)
def test_a_listing_numbered_across_the_show_against_a_seasoned_feed(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    runs: list[Round],
    expected: list[int],
) -> None:
    """Partly not covered: a run reading one source pairs the listing's 5 by number.

    Read alone, the feed's season 2 episode 5 takes the listing's "5-opening-
    night" row, and season 1 episode 5 becomes a second "Opening Night" row;
    read alone, the listing's 5 takes the first stored row numbered 5. Main
    gives 2, 2, 2, 2 and 1, 2, 2 for those two, and 2, 2, 2, 2 and 1, 1, 1 when
    every later run reads both sources.
    """
    podcast = show(db_session, listing=True)

    assert_that(counts(db_session, monkeypatch, podcast, runs)).is_equal_to(expected)
