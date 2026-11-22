"""Feed items keep their own rows: seasons, shared links and GUIDs (ADR 0020).

Every test reads a synthetic feed through the real feed reader and the real
orchestrator; nothing is fetched from the network.
"""

from datetime import timedelta

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Podcast
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_rss import link_key
from tests.feed_identity_support import (
    DAY,
    SITE,
    FeedItem,
    Round,
    discover,
    feed_reader,
    feed_xml,
    listing_url,
    show,
    store,
    stored,
    two_seasons,
)


def _runs(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    items: list[FeedItem],
    *,
    across_runs: bool,
) -> None:
    """Read ``items`` in one run, or one more item per run, then all again."""
    if across_runs:
        for count in range(1, len(items) + 1):
            discover(db, monkeypatch, podcast, Round(items=items[:count]))
    discover(db, monkeypatch, podcast, Round(items=items))
    if across_runs:
        discover(db, monkeypatch, podcast, Round(items=items))


def _guid_by_title(db: Session, podcast: Podcast) -> dict[str, str | None]:
    """Return each stored row's GUID by its title; titles are unique here."""
    rows = stored(db, podcast)
    by_title = {row.title: row.rss_guid for row in rows}
    assert_that(by_title).is_length(len(rows))
    return by_title


_RUNS = pytest.mark.parametrize("across_runs", [False, True], ids=["one-run", "runs"])


@_RUNS
@pytest.mark.parametrize("with_guids", [True, False], ids=["guids", "no-guids"])
def test_same_number_in_two_seasons_is_two_episodes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
    with_guids: bool,
) -> None:
    """Season 1 episode 5 and season 2 episode 5 are stored as two episodes."""
    podcast = show(db_session)
    items = two_seasons(guids=with_guids)

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = stored(db_session, podcast)
    assert_that(
        sorted((row.title, row.season_number, row.episode_number) for row in rows),
    ).is_equal_to([("The Arrival", 1, 5), ("The Departure", 2, 5)])


@_RUNS
def test_seasons_alone_tell_items_without_links_or_guids_apart(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
) -> None:
    """Only the season separates two items that link to the show's homepage."""
    podcast = show(db_session)
    items = [
        FeedItem("Opening Night", link=SITE, season="1", episode="1"),
        FeedItem(
            "Opening Night",
            link=SITE,
            season="2",
            episode="1",
            published_at=DAY + timedelta(days=1),
        ),
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = stored(db_session, podcast)
    assert_that(sorted(row.season_number or 0 for row in rows)).is_equal_to([1, 2])
    assert_that([row.episode_url for row in rows]).contains_only(None)


@_RUNS
@pytest.mark.parametrize(
    "link",
    [SITE, "https://SHOW.example", "https://show.example/listen"],
    ids=["site-link", "site-link-spelled-differently", "shared-link"],
)
def test_items_linking_to_the_homepage_are_separate_episodes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
    link: str,
) -> None:
    """Items sharing one link are stored apart and keep their own GUIDs."""
    podcast = show(db_session)
    items = [
        FeedItem(title, guid=f"urn:show:{index}", link=link, published_at=day)
        for index, (title, day) in enumerate(
            [
                ("Why We Started", DAY),
                ("Listener Mail", DAY + timedelta(days=2)),
                ("A Long Goodbye", DAY + timedelta(days=30)),
            ],
        )
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {
            "Why We Started": "urn:show:0",
            "Listener Mail": "urn:show:1",
            "A Long Goodbye": "urn:show:2",
        },
    )


def test_no_guid_is_written_onto_an_item_sharing_the_homepage_link(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later item's GUID never lands on an earlier item without one."""
    podcast = show(db_session)
    first = FeedItem("Why We Started", link=SITE)
    second = FeedItem(
        "Listener Mail",
        guid="urn:show:mail",
        link=SITE,
        published_at=DAY + timedelta(days=2),
    )

    discover(db_session, monkeypatch, podcast, Round(items=[first]))
    discover(db_session, monkeypatch, podcast, Round(items=[first, second]))

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"Why We Started": None, "Listener Mail": "urn:show:mail"},
    )


def test_a_row_stored_with_the_homepage_link_takes_no_other_item(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that already carries the homepage as its URL finds no new item by it."""
    podcast = show(db_session)
    store(
        db_session,
        podcast,
        title="Why We Started",
        episode_url=SITE,
        published_at=DAY,
    )
    items = [
        FeedItem("Why We Started", link=SITE),
        FeedItem(
            "Listener Mail",
            guid="urn:show:mail",
            link=SITE,
            published_at=DAY + timedelta(days=2),
        ),
    ]

    discover(db_session, monkeypatch, podcast, Round(items=items))

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"Why We Started": None, "Listener Mail": "urn:show:mail"},
    )


@_RUNS
@pytest.mark.parametrize(
    ("guid", "links"),
    [
        ("urn:show:feed", True),
        ("urn:show:feed", False),
        (SITE, True),
        ("urn:show:FEED", False),
    ],
    ids=[
        "one-guid-unique-links",
        "one-guid-homepage-links",
        "homepage-as-guid",
        "one-guid-differing-in-case",
    ],
)
def test_a_guid_shared_by_items_identifies_none_of_them(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
    guid: str,
    links: bool,
) -> None:
    """Items reusing one GUID are stored apart, and none of them keeps it."""
    podcast = show(db_session)
    titles = ["Why We Started", "Listener Mail", "A Long Goodbye"]
    items = [
        FeedItem(
            title,
            guid=guid.lower() if index == 0 else guid,
            link=f"{SITE}{index}" if links else SITE,
            published_at=DAY + timedelta(days=10 * index),
        )
        for index, title in enumerate(titles)
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    by_title = _guid_by_title(db_session, podcast)
    assert_that(sorted(by_title)).is_equal_to(sorted(titles))
    # A single-item first run cannot know the GUID is shared; later items
    # never take it.
    assert_that([by_title[title] for title in titles[1:]]).contains_only(None)
    if not across_runs or guid == SITE:
        assert_that(by_title[titles[0]]).is_none()


@_RUNS
def test_items_of_one_feed_with_distinct_guids_never_merge_by_title(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
) -> None:
    """ "Morning Briefing" on four consecutive days is four episodes."""
    podcast = show(db_session)
    items = [
        FeedItem(
            "Morning Briefing",
            guid=f"urn:show:briefing:{day}",
            link=f"{SITE}briefing/{day}",
            published_at=DAY + timedelta(days=day),
        )
        for day in range(4)
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = stored(db_session, podcast)
    assert_that(sorted(row.rss_guid or "" for row in rows)).is_equal_to(
        [f"urn:show:briefing:{day}" for day in range(4)],
    )


def test_a_guid_differing_only_in_case_finds_its_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feed that changes the case of a GUID keeps one row; the stored GUID stays."""
    podcast = show(db_session)
    before = FeedItem(
        "The Return",
        guid="URN:UUID:7D3E-AB",
        link=f"{SITE}old/return",
    )
    after = FeedItem(
        "The Return (Remastered)",
        guid="urn:uuid:7d3e-ab",
        link=f"{SITE}new/return",
    )

    discover(db_session, monkeypatch, podcast, Round(items=[before]))
    discover(db_session, monkeypatch, podcast, Round(items=[after]))

    rows = stored(db_session, podcast)
    assert_that([(row.title, row.rss_guid) for row in rows]).is_equal_to(
        [("The Return", "URN:UUID:7D3E-AB")],
    )


def test_guids_differing_only_in_case_are_one_episode_in_one_run(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Records whose GUIDs differ only in case and spacing are grouped together."""
    podcast = show(db_session)
    records = [
        DiscoveredEpisode(
            title=title,
            rss_guid=guid,
            episode_url=f"{SITE}{index}",
            discovery_source="rss",
        )
        for index, (title, guid) in enumerate(
            [("The Return", "URN:UUID:7D3E-AB"), ("Retitled", " urn:uuid:7d3e-ab ")],
        )
    ]

    rows = discover(db_session, monkeypatch, podcast, Round(records=records))

    assert_that(rows).is_equal_to(1)


def test_a_feed_with_unique_links_and_guids_behaves_as_before(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unique links and GUIDs without seasons are stored and found as before."""
    podcast = show(db_session)
    items = [
        FeedItem(
            title,
            guid=f"urn:show:{number}",
            link=f"{SITE}episodes/{number}",
            episode=str(number) if number else None,
            published_at=DAY + timedelta(days=7 * number),
        )
        for number, title in enumerate(
            ["Trailer", "Protect Our Parks 1", "Protect Our Parks 2", "Mailbag"],
        )
    ]

    for run in (items[:2], items, items):
        discover(db_session, monkeypatch, podcast, Round(items=run))

    rows = stored(db_session, podcast)
    assert_that(
        [
            (
                row.title,
                row.episode_number,
                row.season_number,
                row.rss_guid,
                row.episode_url,
            )
            for row in rows
        ],
    ).is_equal_to(
        [
            ("Trailer", None, None, "urn:show:0", f"{SITE}episodes/0"),
            ("Protect Our Parks 1", 1, None, "urn:show:1", f"{SITE}episodes/1"),
            ("Protect Our Parks 2", 2, None, "urn:show:2", f"{SITE}episodes/2"),
            ("Mailbag", 3, None, "urn:show:3", f"{SITE}episodes/3"),
        ],
    )


def _stub_podcast() -> Podcast:
    """Return an unsaved podcast that only carries a feed URL."""
    return Podcast(name="Feed Show", slug="feed-show", rss_url="https://f.example")


def test_the_reader_passes_unique_links_and_guids_through() -> None:
    """Each item keeps its link and GUID as written when no other item shares them."""
    xml = feed_xml(
        [
            FeedItem("One", guid="URN:Show:1", link="https://show.example/1/"),
            FeedItem("Two", guid="urn:show:2", link="https://show.example/2#t=0"),
        ],
    )

    episodes = feed_reader(xml).discover_episodes(_stub_podcast())

    assert_that(
        [(ep.episode_url, ep.rss_guid, ep.season_number) for ep in episodes],
    ).is_equal_to(
        [
            ("https://show.example/1/", "URN:Show:1", None),
            ("https://show.example/2#t=0", "urn:show:2", None),
        ],
    )


def test_links_unique_only_by_fragment_count_as_the_site_link() -> None:
    """Fragments are ignored, so "/#ep1" and "/#ep2" both name the site (ADR 0020)."""
    xml = feed_xml(
        [
            FeedItem("One", guid="urn:show:1", link=f"{SITE}#ep1"),
            FeedItem("Two", guid="urn:show:2", link=f"{SITE}#ep2"),
        ],
    )

    episodes = feed_reader(xml).discover_episodes(_stub_podcast())

    assert_that([ep.episode_url for ep in episodes]).is_equal_to([None, None])


def test_shared_values_are_counted_over_the_whole_feed_before_the_window() -> None:
    """An older item outside ``since`` still makes a link and GUID shared."""
    xml = feed_xml(
        [
            FeedItem(
                "Old Episode",
                guid="urn:show:feed",
                link=f"{SITE}listen",
                published_at=DAY - timedelta(days=60),
            ),
            FeedItem("New Episode", guid="urn:show:feed", link=f"{SITE}listen"),
        ],
    )

    episodes = feed_reader(xml).discover_episodes(
        _stub_podcast(),
        since=DAY - timedelta(days=1),
    )

    assert_that(
        [(ep.title, ep.episode_url, ep.rss_guid) for ep in episodes],
    ).is_equal_to([("New Episode", None, None)])


@pytest.mark.parametrize(
    ("season", "expected"),
    [("3", 3), ("0", None), ("-1", None), ("two", None), (None, None)],
    ids=["season", "zero", "negative", "not-a-number", "missing"],
)
def test_the_reader_carries_the_season(
    season: str | None,
    expected: int | None,
) -> None:
    """A positive whole ``itunes:season`` is carried; anything else means none."""
    xml = feed_xml([FeedItem("One", season=season, episode="5")])

    episodes = feed_reader(xml).discover_episodes(_stub_podcast())

    assert_that([(ep.season_number, ep.episode_number) for ep in episodes]).is_equal_to(
        [(expected, 5)],
    )


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("https://show.example/", "https://SHOW.example", True),
        ("https://show.example/a/", " https://show.example/a#top", True),
        ("https://show.example/a", "https://show.example/A", False),
        ("https://show.example/a?p=1", "https://show.example/a?p=2", False),
        (" http://[::1/a ", "http://[::1/a", True),
        ("http://[::1/a", "http://[::1/b", False),
    ],
    ids=[
        "host-case-and-slash",
        "space-and-fragment",
        "path-case",
        "query",
        "unreadable-link-as-written",
        "unreadable-links-differ",
    ],
)
def test_link_key(first: str, second: str, same: bool) -> None:
    """Links compare without host case, a trailing slash or a fragment."""
    assert_that(link_key(first) == link_key(second)).is_equal_to(same)
    assert_that(link_key("  ")).is_none()


@pytest.mark.parametrize(
    ("slug", "number", "season"),
    [("105-the-return", 105, None), ("5-the-return", 5, 2), ("the-return", 5, 2)],
    ids=["listing-number-differs", "listing-number-equal", "listing-unnumbered"],
)
def test_a_merged_record_keeps_the_season_only_with_its_number(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    number: int,
    season: int | None,
) -> None:
    """The season of the feed's episode 5 never qualifies the listing's 105."""
    podcast = show(db_session, listing=True)
    item = FeedItem(
        "The Return",
        guid="urn:show:return",
        link=f"{SITE}return",
        season="2",
        episode="5",
    )

    discover(
        db_session,
        monkeypatch,
        podcast,
        Round(items=[item], links={slug: "The Return"}),
    )

    rows = stored(db_session, podcast)
    assert_that(
        [(row.episode_url, row.episode_number, row.season_number) for row in rows],
    ).is_equal_to([(listing_url(slug), number, season)])
