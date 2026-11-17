"""Feed items keep their own rows: seasons, shared links and GUIDs (ADR 0019).

Every test reads a synthetic feed through the real feed reader and the real
orchestrator; nothing is fetched from the network.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast
from podex.services import discovery_orchestrator
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_rss import RSSDiscovery, link_key
from podex.services.feed_http import FeedHttpClient
from tests.conftest import persist_episode

_SITE = "https://show.example/"
_FEED_URL = "https://feeds.example/show"
_DAY = datetime(2026, 5, 4, 6, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Item:
    """One feed item; ``None`` leaves the element out."""

    title: str
    guid: str | None = None
    link: str | None = None
    season: str | None = None
    episode: str | None = None
    published_at: datetime = _DAY


def _feed_xml(items: list[_Item], *, site: str = _SITE) -> str:
    """Return an RSS document holding ``items`` under the show's site link."""
    parts: list[str] = []
    for item in items:
        parts.append(f"<item><title>{escape(item.title)}</title>")
        if item.guid is not None:
            parts.append(f'<guid isPermaLink="false">{escape(item.guid)}</guid>')
        if item.link is not None:
            parts.append(f"<link>{escape(item.link)}</link>")
        if item.season is not None:
            parts.append(f"<itunes:season>{item.season}</itunes:season>")
        if item.episode is not None:
            parts.append(f"<itunes:episode>{item.episode}</itunes:episode>")
        published = item.published_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        parts.append(f"<pubDate>{published}</pubDate></item>")
    return (
        '<?xml version="1.0"?>'
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>Feed Show</title><link>{escape(site)}</link>"
        f"{''.join(parts)}</channel></rss>"
    )


def _reader(xml: str) -> RSSDiscovery:
    """Return the real feed reader serving ``xml`` without network traffic."""

    async def resolve(host: str, port: int) -> list[str]:
        """Return a public address without DNS traffic."""
        del host, port
        return ["93.184.216.34"]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(xml.encode())),
    )
    return RSSDiscovery(FeedHttpClient(resolver=resolve, transport=transport))


def _podcast(db: Session) -> Podcast:
    """Persist a podcast read only from its feed."""
    podcast = Podcast(name="Feed Show", slug="feed-show", rss_url=_FEED_URL)
    db.add(podcast)
    db.commit()
    return podcast


def _run(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    items: list[_Item],
) -> None:
    """Run one discovery that reads ``items`` from the podcast's feed."""
    xml = _feed_xml(items)
    monkeypatch.setattr(discovery_orchestrator, "RSSDiscovery", lambda: _reader(xml))
    with DiscoveryOrchestrator(db) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)
    assert_that(result.errors).is_empty()


def _runs(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    items: list[_Item],
    *,
    across_runs: bool,
) -> None:
    """Read ``items`` in one run, or one more item per run, then all again."""
    if across_runs:
        for count in range(1, len(items) + 1):
            _run(db, monkeypatch, podcast, items[:count])
    _run(db, monkeypatch, podcast, items)
    if not across_runs:
        return
    _run(db, monkeypatch, podcast, items)


def _rows(db: Session, podcast: Podcast) -> list[Episode]:
    """Return the podcast's stored episodes in creation order."""
    return list(
        db.scalars(
            select(Episode)
            .where(Episode.podcast_id == podcast.id)
            .order_by(Episode.id),
        ).all(),
    )


def _guid_by_title(db: Session, podcast: Podcast) -> dict[str, str | None]:
    """Return each stored row's GUID by its title; titles are unique here."""
    rows = _rows(db, podcast)
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
    podcast = _podcast(db_session)
    items = [
        _Item(
            "The Arrival",
            guid="urn:show:s1e5" if with_guids else None,
            link=f"{_SITE}s1e5",
            season="1",
            episode="5",
            published_at=_DAY - timedelta(days=300),
        ),
        _Item(
            "The Departure",
            guid="urn:show:s2e5" if with_guids else None,
            link=f"{_SITE}s2e5",
            season="2",
            episode="5",
        ),
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = _rows(db_session, podcast)
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
    podcast = _podcast(db_session)
    items = [
        _Item("Opening Night", link=_SITE, season="1", episode="1"),
        _Item(
            "Opening Night",
            link=_SITE,
            season="2",
            episode="1",
            published_at=_DAY + timedelta(days=1),
        ),
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = _rows(db_session, podcast)
    assert_that(sorted(row.season_number or 0 for row in rows)).is_equal_to([1, 2])
    assert_that([row.episode_url for row in rows]).contains_only(None)


@_RUNS
@pytest.mark.parametrize(
    "link",
    [_SITE, "https://SHOW.example", "https://show.example/listen"],
    ids=["site-link", "site-link-spelled-differently", "shared-link"],
)
def test_items_linking_to_the_homepage_are_separate_episodes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
    link: str,
) -> None:
    """Items sharing one link are stored apart and keep their own GUIDs."""
    podcast = _podcast(db_session)
    items = [
        _Item(title, guid=f"urn:show:{index}", link=link, published_at=day)
        for index, (title, day) in enumerate(
            [
                ("Why We Started", _DAY),
                ("Listener Mail", _DAY + timedelta(days=2)),
                ("A Long Goodbye", _DAY + timedelta(days=30)),
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
    podcast = _podcast(db_session)
    first = _Item("Why We Started", link=_SITE)
    second = _Item(
        "Listener Mail",
        guid="urn:show:mail",
        link=_SITE,
        published_at=_DAY + timedelta(days=2),
    )

    _run(db_session, monkeypatch, podcast, [first])
    _run(db_session, monkeypatch, podcast, [first, second])

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"Why We Started": None, "Listener Mail": "urn:show:mail"},
    )


def test_a_row_stored_with_the_homepage_link_takes_no_other_item(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that already carries the homepage as its URL finds no new item by it."""
    podcast = _podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="Why We Started",
        episode_url=_SITE,
        published_at=_DAY,
    )
    db_session.commit()
    items = [
        _Item("Why We Started", link=_SITE),
        _Item(
            "Listener Mail",
            guid="urn:show:mail",
            link=_SITE,
            published_at=_DAY + timedelta(days=2),
        ),
    ]

    _run(db_session, monkeypatch, podcast, items)

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"Why We Started": None, "Listener Mail": "urn:show:mail"},
    )


@_RUNS
@pytest.mark.parametrize(
    ("guid", "links"),
    [
        ("urn:show:feed", True),
        ("urn:show:feed", False),
        (_SITE, True),
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
    podcast = _podcast(db_session)
    titles = ["Why We Started", "Listener Mail", "A Long Goodbye"]
    items = [
        _Item(
            title,
            guid=guid.lower() if index == 0 else guid,
            link=f"{_SITE}{index}" if links else _SITE,
            published_at=_DAY + timedelta(days=10 * index),
        )
        for index, title in enumerate(titles)
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    by_title = _guid_by_title(db_session, podcast)
    assert_that(sorted(by_title)).is_equal_to(sorted(titles))
    # A single-item first run cannot know the GUID is shared; later items
    # never take it.
    assert_that([by_title[title] for title in titles[1:]]).contains_only(None)
    if not across_runs or guid == _SITE:
        assert_that(by_title[titles[0]]).is_none()


@_RUNS
def test_items_of_one_feed_with_distinct_guids_never_merge_by_title(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    across_runs: bool,
) -> None:
    """ "Morning Briefing" on four consecutive days is four episodes."""
    podcast = _podcast(db_session)
    items = [
        _Item(
            "Morning Briefing",
            guid=f"urn:show:briefing:{day}",
            link=f"{_SITE}briefing/{day}",
            published_at=_DAY + timedelta(days=day),
        )
        for day in range(4)
    ]

    _runs(db_session, monkeypatch, podcast, items, across_runs=across_runs)

    rows = _rows(db_session, podcast)
    assert_that(sorted(row.rss_guid or "" for row in rows)).is_equal_to(
        [f"urn:show:briefing:{day}" for day in range(4)],
    )


def test_a_guid_differing_only_in_case_finds_its_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feed that changes the case of a GUID keeps one row; the stored GUID stays."""
    podcast = _podcast(db_session)
    before = _Item("The Return", guid="URN:UUID:7D3E-AB", link=f"{_SITE}old/return")
    after = _Item(
        "The Return (Remastered)",
        guid="urn:uuid:7d3e-ab",
        link=f"{_SITE}new/return",
    )

    _run(db_session, monkeypatch, podcast, [before])
    _run(db_session, monkeypatch, podcast, [after])

    rows = _rows(db_session, podcast)
    assert_that([(row.title, row.rss_guid) for row in rows]).is_equal_to(
        [("The Return", "URN:UUID:7D3E-AB")],
    )


def test_guids_differing_only_in_case_are_one_episode_in_one_run(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Records whose GUIDs differ only in case and spacing are grouped together."""
    podcast = _podcast(db_session)
    records = [
        DiscoveredEpisode(
            title=title,
            rss_guid=guid,
            episode_url=f"{_SITE}{index}",
            discovery_source="rss",
        )
        for index, (title, guid) in enumerate(
            [("The Return", "URN:UUID:7D3E-AB"), ("Retitled", " urn:uuid:7d3e-ab ")],
        )
    ]
    monkeypatch.setattr(
        RSSDiscovery,
        "discover_episodes",
        lambda self, podcast, since=None: records,
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(1)


@pytest.mark.parametrize(
    ("stored", "reported", "found"),
    [
        ("  URN:Show:Return\t", "urn:show:return", True),
        ("urn:show:return", "URN:SHOW:RETURN", True),
        ("urn:show:\u00c9", "urn:show:\u00e9", False),
        ("urn:show:\u00e9", "URN:SHOW:\u00e9", True),
        ("urn:show:50%_off", "URN:SHOW:50%_OFF", True),
        ("urn:show:50xyoff", "urn:show:50%_off", False),
        ("   ", "   ", False),
    ],
    ids=[
        "whitespace-and-case",
        "case",
        "non-ascii-case-is-exact",
        "ascii-case-around-non-ascii",
        "like-characters-literal",
        "like-characters-not-wildcards",
        "blank-identifies-nothing",
    ],
)
def test_catalog_lookup_compares_guids_by_the_rule(
    db_session: Session,
    stored: str,
    reported: str,
    found: bool,
) -> None:
    """The lookup finds a stored GUID by surrounding whitespace and ASCII case only."""
    podcast = _podcast(db_session)
    row = persist_episode(
        db_session,
        podcast=podcast,
        title="Stored",
        rss_guid=stored,
    )
    db_session.commit()
    record = DiscoveredEpisode(
        title="Unrelated",
        rss_guid=reported,
        discovery_source="rss",
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private catalog lookup.
        # pylint: disable-next=protected-access
        existing = orchestrator._find_existing_episode(podcast, record)

    assert_that(existing).is_equal_to(row if found else None)


def test_catalog_lookup_prefers_an_exactly_equal_guid(db_session: Session) -> None:
    """A row with the GUID as written wins over an earlier row differing in case."""
    podcast = _podcast(db_session)
    persist_episode(db_session, podcast=podcast, title="Upper", rss_guid="URN:X")
    exact = persist_episode(
        db_session, podcast=podcast, title="Lower", rss_guid="urn:x"
    )
    db_session.commit()

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private catalog lookup.
        # pylint: disable-next=protected-access
        existing = orchestrator._find_existing_episode(
            podcast,
            DiscoveredEpisode(title="Lower", rss_guid="urn:x"),
        )

    assert_that(existing).is_equal_to(exact)


@pytest.mark.parametrize(
    ("stored_seasons", "season", "expected"),
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
def test_catalog_lookup_by_number_respects_the_season(
    db_session: Session,
    stored_seasons: tuple[int | None, ...],
    season: int | None,
    expected: int | None,
) -> None:
    """Number 5 finds a row of the record's season first, then one without a season."""
    podcast = _podcast(db_session)
    rows = [
        persist_episode(
            db_session,
            podcast=podcast,
            title=f"Stored {index}",
            episode_number=5,
            season_number=stored,
        )
        for index, stored in enumerate(stored_seasons)
    ]
    db_session.commit()
    record = DiscoveredEpisode(title="New", episode_number=5, season_number=season)

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private catalog lookup.
        # pylint: disable-next=protected-access
        existing = orchestrator._find_existing_episode(podcast, record)

    assert_that(existing).is_equal_to(None if expected is None else rows[expected])


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
    stored_guid: str | None,
    record_guid: str | None,
    found: bool,
) -> None:
    """A row stored before seasons were is not another feed item's by number."""
    podcast = _podcast(db_session)
    row = persist_episode(
        db_session,
        podcast=podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid=stored_guid,
    )
    db_session.commit()
    record = DiscoveredEpisode(
        title="The Departure",
        episode_number=5,
        season_number=2,
        rss_guid=record_guid,
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private catalog lookup.
        # pylint: disable-next=protected-access
        existing = orchestrator._find_existing_episode(podcast, record)

    assert_that(existing).is_equal_to(row if found else None)


def test_season_two_is_stored_beside_a_row_stored_before_seasons(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Season 2 episode 5 gets its own row next to a season-less episode 5 row."""
    podcast = _podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="The Arrival",
        episode_number=5,
        rss_guid="urn:show:s1e5",
        episode_url=f"{_SITE}s1e5",
        published_at=_DAY - timedelta(days=300),
    )
    db_session.commit()
    items = [
        _Item(
            "The Arrival",
            guid="urn:show:s1e5",
            link=f"{_SITE}s1e5",
            season="1",
            episode="5",
            published_at=_DAY - timedelta(days=300),
        ),
        _Item(
            "The Departure",
            guid="urn:show:s2e5",
            link=f"{_SITE}s2e5",
            season="2",
            episode="5",
        ),
    ]

    for _ in range(2):
        _run(db_session, monkeypatch, podcast, items)

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"The Arrival": "urn:show:s1e5", "The Departure": "urn:show:s2e5"},
    )


@pytest.mark.parametrize(
    ("record", "found"),
    [
        (DiscoveredEpisode(title="Why We Started", published_at=_DAY), True),
        (
            DiscoveredEpisode(
                title="Why We Started",
                published_at=_DAY.replace(tzinfo=None),
            ),
            True,
        ),
        (
            DiscoveredEpisode(
                title="Why We Started",
                published_at=_DAY + timedelta(minutes=1),
            ),
            False,
        ),
        (DiscoveredEpisode(title="Why We Started"), False),
        (DiscoveredEpisode(title="Why we started", published_at=_DAY), False),
        (
            DiscoveredEpisode(
                title="Why We Started",
                published_at=_DAY,
                rss_guid="urn:show:other",
            ),
            False,
        ),
        (
            DiscoveredEpisode(
                title="Why We Started",
                published_at=_DAY,
                episode_url=f"{_SITE}other",
            ),
            False,
        ),
    ],
    ids=[
        "same-title-and-time",
        "naive-time-read-as-utc",
        "other-time",
        "undated",
        "other-title",
        "record-with-a-guid",
        "record-with-a-url",
    ],
)
def test_catalog_lookup_by_title_and_time_only_without_identifiers(
    db_session: Session,
    record: DiscoveredEpisode,
    found: bool,
) -> None:
    """A record with no identifier or URL finds the row with its title and time."""
    podcast = _podcast(db_session)
    row = persist_episode(
        db_session,
        podcast=podcast,
        title="Why We Started",
        published_at=_DAY,
        rss_guid="urn:show:shared",
    )
    db_session.commit()

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private catalog lookup.
        # pylint: disable-next=protected-access
        existing = orchestrator._find_existing_episode(podcast, record)

    assert_that(existing).is_equal_to(row if found else None)


def test_a_row_merged_on_main_keeps_its_first_item(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that took a shared GUID earlier is found again by title and time.

    It is not split; the other items it absorbed get rows of their own.
    """
    podcast = _podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="Why We Started",
        published_at=_DAY,
        rss_guid="urn:show:feed",
        episode_url=_SITE,
    )
    db_session.commit()
    items = [
        _Item("Why We Started", guid="urn:show:feed", link=_SITE),
        _Item(
            "Listener Mail",
            guid="urn:show:feed",
            link=_SITE,
            published_at=_DAY + timedelta(days=2),
        ),
    ]

    for _ in range(2):
        _run(db_session, monkeypatch, podcast, items)

    assert_that(_guid_by_title(db_session, podcast)).is_equal_to(
        {"Why We Started": "urn:show:feed", "Listener Mail": None},
    )


def test_a_feed_with_unique_links_and_guids_behaves_as_before(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unique links and GUIDs without seasons are stored and found as before."""
    podcast = _podcast(db_session)
    items = [
        _Item(
            title,
            guid=f"urn:show:{number}",
            link=f"{_SITE}episodes/{number}",
            episode=str(number) if number else None,
            published_at=_DAY + timedelta(days=7 * number),
        )
        for number, title in enumerate(
            ["Trailer", "Protect Our Parks 1", "Protect Our Parks 2", "Mailbag"],
        )
    ]

    _run(db_session, monkeypatch, podcast, items[:2])
    _run(db_session, monkeypatch, podcast, items)
    _run(db_session, monkeypatch, podcast, items)

    rows = _rows(db_session, podcast)
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
            ("Trailer", None, None, "urn:show:0", f"{_SITE}episodes/0"),
            ("Protect Our Parks 1", 1, None, "urn:show:1", f"{_SITE}episodes/1"),
            ("Protect Our Parks 2", 2, None, "urn:show:2", f"{_SITE}episodes/2"),
            ("Mailbag", 3, None, "urn:show:3", f"{_SITE}episodes/3"),
        ],
    )


def test_the_reader_passes_unique_links_and_guids_through() -> None:
    """Each item keeps its link and GUID as written when no other item shares them."""
    xml = _feed_xml(
        [
            _Item("One", guid="URN:Show:1", link="https://show.example/1/"),
            _Item("Two", guid="urn:show:2", link="https://show.example/2#t=0"),
        ],
    )
    podcast = Podcast(name="Feed Show", slug="feed-show", rss_url="https://f.example")

    episodes = _reader(xml).discover_episodes(podcast)

    assert_that(
        [(ep.episode_url, ep.rss_guid, ep.season_number) for ep in episodes],
    ).is_equal_to(
        [
            ("https://show.example/1/", "URN:Show:1", None),
            ("https://show.example/2#t=0", "urn:show:2", None),
        ],
    )


@pytest.mark.parametrize(
    ("season", "expected"),
    [("3", 3), ("0", None), ("-1", None), ("two", None), (None, None)],
    ids=["season", "zero", "negative", "not-a-number", "missing"],
)
def test_the_reader_carries_the_season(
    season: str | None, expected: int | None
) -> None:
    """A positive whole ``itunes:season`` is carried; anything else means none."""
    xml = _feed_xml([_Item("One", season=season, episode="5")])
    podcast = Podcast(name="Feed Show", slug="feed-show", rss_url="https://f.example")

    episodes = _reader(xml).discover_episodes(podcast)

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
    ("listing_number", "season"),
    [(105, None), (5, 2), (None, 2)],
    ids=["listing-number-differs", "listing-number-equal", "listing-unnumbered"],
)
def test_a_merged_record_keeps_the_season_only_with_its_number(
    db_session: Session,
    listing_number: int | None,
    season: int | None,
) -> None:
    """The season of the feed's episode 5 never qualifies the listing's 105."""
    listing = DiscoveredEpisode(
        title="The Return",
        episode_number=listing_number,
        episode_url="https://podscripts.co/podcasts/show/the-return",
        has_transcript=True,
        discovery_source="podscripts",
    )
    feed = DiscoveredEpisode(
        title="The Return",
        episode_number=5,
        season_number=2,
        rss_guid="urn:show:return",
        discovery_source="rss",
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private merge.
        # pylint: disable-next=protected-access
        merged = orchestrator._deduplicate_episodes([listing, feed])

    assert_that([(ep.episode_number, ep.season_number) for ep in merged]).is_equal_to(
        [(listing_number or 5, season)],
    )
