"""Tests for idempotent episode discovery persistence."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from assertpy import assert_that
from bs4 import BeautifulSoup, Tag
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import DiscoverySource, Episode, Podcast
from podex.services import discovery_podscripts
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from tests.conftest import persist_episode


def test_upsert_matches_unnumbered_episode_by_provider_url(
    db_session: Session,
) -> None:
    """Verify repeated provider discovery does not duplicate unnumbered episodes."""
    podcast = Podcast(name="Crime Junkie", slug="crime-junkie")
    db_session.add(podcast)
    db_session.commit()
    discovered = DiscoveredEpisode(
        title="An Unnumbered Episode",
        episode_url="https://podscripts.co/podcasts/crime-junkie/one-case",
        has_transcript=True,
        discovery_source="podscripts",
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private normalization/protocol boundary.
        # pylint: disable-next=protected-access
        created = orchestrator._upsert_episodes(podcast, [discovered])
        # Direct unit coverage of the private normalization/protocol boundary.
        # pylint: disable-next=protected-access
        repeated = orchestrator._upsert_episodes(podcast, [discovered])

    assert_that(created).is_equal_to((1, 0))
    assert_that(repeated).is_equal_to((0, 0))
    assert_that(db_session.query(Episode).count()).is_equal_to(1)


def test_deduplicate_matches_repeated_provider_urls(db_session: Session) -> None:
    """Verify duplicate provider links in discovery collapse to one episode."""
    first = DiscoveredEpisode(
        title="Episode listing title",
        episode_url="https://podscripts.co/podcasts/pbd-podcast/a-conversation",
        has_transcript=True,
        discovery_source="podscripts",
    )
    repeated = DiscoveredEpisode(
        title="Episode page title",
        episode_url=first.episode_url,
        has_transcript=True,
        discovery_source="podscripts",
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        # Direct unit coverage of the private normalization/protocol boundary.
        # pylint: disable-next=protected-access
        episodes = orchestrator._deduplicate_episodes([first, repeated])

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].episode_url).is_equal_to(first.episode_url)


def test_podscripts_episode_comment_link_falls_back_to_url_title() -> None:
    """Verify comment-count link text cannot become an episode title."""
    tag = BeautifulSoup(
        '<a href="/podcasts/example-show/42-pilot">0comments</a>',
        "html.parser",
    ).find("a")
    assert_that(tag).is_instance_of(Tag)
    link = cast("Tag", tag)

    source = PodscriptsDiscovery(delay=0)
    try:
        # Direct unit coverage of the private normalization/protocol boundary.
        # pylint: disable-next=protected-access
        episode = source._parse_episode_link(
            link,
            "/podcasts/example-show/42-pilot",
        )
    finally:
        source.close()

    assert_that(episode).is_not_none()
    parsed = cast("DiscoveredEpisode", episode)
    assert_that(parsed.title).is_equal_to("Pilot")
    assert_that(parsed.episode_number).is_equal_to(42)


def test_upsert_persists_discovered_episode(db_session: Session) -> None:
    """A discovered episode round-trips through the orchestrator upsert."""
    podcast = Podcast(name="Example Show", slug="example-show-upsert")
    db_session.add(podcast)
    db_session.commit()

    orchestrator = DiscoveryOrchestrator(db_session)
    discovered = DiscoveredEpisode(
        title="Pilot",
        rss_guid="urn:example:episode:1",
        discovery_source="rss",
    )
    # Direct unit coverage of the private normalization/protocol boundary.
    # pylint: disable-next=protected-access
    new_count, updated_count = orchestrator._upsert_episodes(
        podcast,
        [discovered],
    )

    assert_that(new_count).is_equal_to(1)
    assert_that(updated_count).is_equal_to(0)
    stored = db_session.execute(select(Episode)).scalar_one()
    assert_that(stored.rss_guid).is_equal_to("urn:example:episode:1")
    assert_that(stored.discovery_source).is_equal_to(DiscoverySource.RSS)


def _serve_listing(
    monkeypatch: pytest.MonkeyPatch,
    links: dict[str, str],
) -> None:
    """Serve ``links`` (href slug to link text) as page 1 of the show's listing."""

    def route(request: httpx.Request) -> httpx.Response:
        """Return the links on page 1 and an empty page after it."""
        if request.url.params["page"] != "1":
            return httpx.Response(200, text="")
        return httpx.Response(
            200,
            text="".join(
                f'<a href="/podcasts/show/{slug}">{text}</a>'
                for slug, text in links.items()
            ),
        )

    monkeypatch.setattr(
        discovery_podscripts,
        "build_podscripts_client",
        lambda: httpx.Client(transport=httpx.MockTransport(route)),
    )
    monkeypatch.setattr(discovery_podscripts.time, "sleep", lambda _seconds: None)


@pytest.mark.parametrize(
    "older_slug",
    ["2580-protect-our-parks-17", "protect-our-parks-17"],
    ids=["both-numbered", "older-unnumbered"],
)
def test_numbered_series_episodes_are_both_stored(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    older_slug: str,
) -> None:
    """Episodes whose titles differ only by their series number stay apart."""
    _serve_listing(
        monkeypatch,
        {
            "2581-protect-our-parks-18": "Protect Our Parks 18",
            older_slug: "Protect Our Parks 17",
        },
    )
    podcast = Podcast(name="Example Show", slug="example-show", podscripts_slug="show")
    db_session.add(podcast)
    db_session.commit()

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(2)
    titles = db_session.scalars(
        select(Episode.title).where(Episode.podcast_id == podcast.id),
    ).all()
    assert_that(sorted(titles)).is_equal_to(
        ["Protect Our Parks 17", "Protect Our Parks 18"],
    )


def _serve_feed(
    monkeypatch: pytest.MonkeyPatch,
    episodes: list[DiscoveredEpisode],
) -> None:
    """Return ``episodes`` from every RSS discovery call."""

    def feed(
        self: RSSDiscovery,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Return the scripted feed entries."""
        del self, podcast, since
        return episodes

    monkeypatch.setattr(RSSDiscovery, "discover_episodes", feed)


def _feed_podcast(db: Session) -> Podcast:
    """Persist a podcast discovered only through its feed."""
    podcast = Podcast(
        name="Feed Show",
        slug="feed-show",
        rss_url="https://feeds.example/show",
    )
    db.add(podcast)
    db.commit()
    return podcast


_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_FEED_DAY = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
_FEED_LINK = "https://show.example/episodes/the-return"


def _listing_url(slug: str) -> str:
    """Return the listing URL the provider builds for ``slug``."""
    return f"https://podscripts.co/podcasts/show/{slug}"


def _two_source_podcast(db: Session) -> Podcast:
    """Persist a podcast read from both its transcript listing and its feed."""
    podcast = Podcast(
        name="Two Source Show",
        slug="two-source-show",
        podscripts_slug="show",
        rss_url="https://feeds.example/show",
    )
    db.add(podcast)
    db.commit()
    return podcast


def _feed_item(
    number: int | None,
    *,
    title: str = "The Return",
    guid: str | None = "urn:example:the-return",
    link: str | None = _FEED_LINK,
    published_at: datetime = _FEED_DAY,
) -> DiscoveredEpisode:
    """Return one dated feed entry."""
    return DiscoveredEpisode(
        title=title,
        episode_number=number,
        published_at=published_at,
        episode_url=link,
        rss_guid=guid,
        discovery_source="rss",
    )


@dataclass(frozen=True, slots=True)
class _Round:
    """What one discovery run reads: listing links, feed entries, its time."""

    links: dict[str, str] = field(default_factory=dict)
    feed: list[DiscoveredEpisode] = field(default_factory=list)
    now: datetime = _NOW
    feed_fails: bool = False


def _run(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    run: _Round,
) -> None:
    """Run one discovery over the round's listing links and feed entries."""
    _serve_listing(monkeypatch, run.links)
    if run.feed_fails:
        monkeypatch.setattr(RSSDiscovery, "discover_episodes", _failing_feed)
    else:
        _serve_feed(monkeypatch, run.feed)
    with DiscoveryOrchestrator(db, clock=lambda: run.now) as orchestrator:
        orchestrator.discover_for_podcast(podcast)


def _failing_feed(
    self: RSSDiscovery,
    podcast: Podcast,
    since: datetime | None = None,
) -> list[DiscoveredEpisode]:
    """Fail as an unreachable feed does."""
    del self, podcast, since
    raise httpx.ConnectError("feed unreachable")


def _rows(db: Session, podcast: Podcast) -> list[Episode]:
    """Return the podcast's stored episodes."""
    return list(
        db.scalars(select(Episode).where(Episode.podcast_id == podcast.id)).all(),
    )


def test_stable_identifier_matches_a_row_numbered_by_another_source(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feed GUID finds its row although the row holds the listing's number.

    The stored number is kept; discovery never rewrites a stored number.
    """
    podcast = _feed_podcast(db_session)
    row = persist_episode(
        db_session,
        podcast=podcast,
        title="The Return",
        episode_number=105,
        episode_url=_listing_url("105-the-return"),
        rss_guid="urn:example:the-return",
    )
    db_session.commit()
    _serve_feed(monkeypatch, [_feed_item(5, link=None)])

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(0)
    assert_that(result.updated_episodes).is_equal_to(1)
    assert_that(_rows(db_session, podcast)).is_length(1)
    db_session.refresh(row)
    assert_that(row.episode_number).is_equal_to(105)
    assert_that(row.published_at).is_not_none()


def test_feed_renumbering_an_item_keeps_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An item renumbered from 5 to 6 with the same GUID and link stays one row."""
    podcast = _feed_podcast(db_session)
    for number in (5, 6):
        _serve_feed(monkeypatch, [_feed_item(number)])
        with DiscoveryOrchestrator(db_session) as orchestrator:
            orchestrator.discover_for_podcast(podcast)

    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that(rows[0].episode_number).is_equal_to(5)


def test_records_sharing_a_guid_in_one_run_are_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two entries with one GUID and different numbers are stored once."""
    podcast = _feed_podcast(db_session)
    _serve_feed(monkeypatch, [_feed_item(5), _feed_item(6, link=None)])

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(1)
    assert_that(_rows(db_session, podcast)).is_length(1)


def test_feed_items_numbered_zero_are_separate_episodes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Episode number 0 means no number, so it joins nothing.

    The items carry no GUIDs, so only their numbers and titles tell them apart.
    """
    titles = ["Trailer", "Bonus: Live Q&A", "Announcement", "Holiday Special"]
    _serve_feed(
        monkeypatch,
        [
            _feed_item(
                0,
                title=title,
                guid=None,
                link=f"https://show.example/{index}",
            )
            for index, title in enumerate(titles)
        ],
    )
    podcast = _feed_podcast(db_session)

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(4)
    assert_that(sorted(row.title for row in _rows(db_session, podcast))).is_equal_to(
        sorted(titles),
    )


def test_second_trailer_numbered_zero_is_stored(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored row numbered 0 is not found by a new item numbered 0."""
    podcast = _feed_podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="Trailer",
        episode_number=0,
        rss_guid="urn:example:trailer",
    )
    db_session.commit()
    _serve_feed(
        monkeypatch,
        [_feed_item(0, title="Season Two Trailer", guid="urn:example:trailer-2")],
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(1)
    assert_that(_rows(db_session, podcast)).is_length(2)


_DIFFERENTLY_NUMBERED = pytest.mark.parametrize(
    "case",
    [
        ("105-the-return", "The Return", 5),
        ("10-things-we-learned", "10 Things We Learned", 347),
        ("2026-year-in-review", "2026 Year In Review", 347),
    ],
    ids=["leading-digits-not-the-feed-number", "title-starts-with-a-number", "year"],
)


@_DIFFERENTLY_NUMBERED
def test_sources_numbering_one_episode_differently_store_it_once(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, int],
) -> None:
    """Listing and feed numbers that differ do not split one episode in one run."""
    slug, title, feed_number = case
    podcast = _two_source_podcast(db_session)

    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links={slug: title}, feed=[_feed_item(feed_number, title=title)]),
    )

    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that(rows[0].episode_url).is_equal_to(_listing_url(slug))
    assert_that(rows[0].rss_guid).is_equal_to("urn:example:the-return")


@_DIFFERENTLY_NUMBERED
@pytest.mark.parametrize("listing_first", [True, False], ids=["listing", "feed"])
def test_source_seen_first_in_an_earlier_run_still_gives_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, int],
    listing_first: bool,
) -> None:
    """A later run that reads both sources finds the row the first source made."""
    slug, title, feed_number = case
    podcast = _two_source_podcast(db_session)
    links = {slug: title}
    feed = [_feed_item(feed_number, title=title)]

    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links=links) if listing_first else _Round(feed=feed),
    )
    _run(db_session, monkeypatch, podcast, _Round(links=links, feed=feed))

    assert_that(_rows(db_session, podcast)).is_length(1)


def test_renumbered_episode_leaves_one_row_per_listing_url(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renumbering in the feed adds no row, so the complete walk is recorded.

    The listing link carries no number, so the row takes the feed's number.
    """
    podcast = _two_source_podcast(db_session)
    links = {"the-return": "The Return"}

    for number in (5, 6):
        _run(
            db_session,
            monkeypatch,
            podcast,
            _Round(links=links, feed=[_feed_item(number)]),
        )

    listing_url = _listing_url("the-return")
    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that([row.episode_url for row in rows]).is_equal_to([listing_url])
    assert_that(podcast.podscripts_listing_walked_at).is_not_none()


_LATER = (_NOW, _NOW + timedelta(hours=1), _NOW + timedelta(hours=2))
"""Start times of consecutive runs, so each run's walk record is visible."""


def _assert_one_row_per_listing_url(db: Session, podcast: Podcast) -> None:
    """Assert that no listing URL is stored on two rows of ``podcast``."""
    listing_urls = [
        row.episode_url
        for row in _rows(db, podcast)
        if row.episode_url and row.episode_url.startswith(_listing_url(""))
    ]
    assert_that(listing_urls).does_not_contain_duplicates()


def _walked_at(podcast: Podcast) -> datetime | None:
    """Return the podcast's complete-walk record in UTC."""
    walked_at = podcast.podscripts_listing_walked_at
    return walked_at.replace(tzinfo=UTC) if walked_at is not None else None


@pytest.mark.parametrize("feed_fails", [False, True], ids=["dropped", "failed"])
def test_feed_first_row_without_a_link_stays_one_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    feed_fails: bool,
) -> None:
    """A feed-first row is found by its listing URL once the feed stops reporting it.

    The run reading both sources writes the listing URL onto the feed's row
    (it has none); the listing record alone then finds that row by URL.
    """
    podcast = _two_source_podcast(db_session)
    links = {"105-the-return": "The Return"}
    item = _feed_item(5, link=None)

    _run(db_session, monkeypatch, podcast, _Round(feed=[item], now=_LATER[0]))
    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links=links, feed=[item], now=_LATER[1]),
    )
    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links=links, now=_LATER[2], feed_fails=feed_fails),
    )

    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that(rows[0].episode_url).is_equal_to(_listing_url("105-the-return"))
    assert_that(rows[0].rss_guid).is_equal_to("urn:example:the-return")
    _assert_one_row_per_listing_url(db_session, podcast)
    assert_that(_walked_at(podcast)).is_equal_to(_LATER[2])


@pytest.mark.parametrize(
    ("first_guid", "second_guid"),
    [(None, None), ("urn:example:first", "urn:example:second")],
    ids=["feed-without-guids", "guid-changes-with-the-number"],
)
def test_feed_renumbering_keeps_the_listing_row(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    first_guid: str | None,
    second_guid: str | None,
) -> None:
    """A renumbered feed item keeps one row through the unchanged listing URL."""
    podcast = _two_source_podcast(db_session)
    links = {"the-return": "The Return"}

    for now, number, guid in ((_LATER[0], 5, first_guid), (_LATER[1], 6, second_guid)):
        _run(
            db_session,
            monkeypatch,
            podcast,
            _Round(links=links, feed=[_feed_item(number, guid=guid)], now=now),
        )

    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that(rows[0].episode_url).is_equal_to(_listing_url("the-return"))
    _assert_one_row_per_listing_url(db_session, podcast)
    assert_that(_walked_at(podcast)).is_equal_to(_LATER[1])


@pytest.mark.parametrize("tenth_first", [True, False], ids=["tenth-first", "ten-first"])
def test_feed_numbers_pair_each_listing_row_with_its_own_item(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tenth_first: bool,
) -> None:
    """Each feed item's GUID and date land on its own listing row.

    The feed numbers "The Tenth One" 10, which equals the leading number of
    the listing's "10-things-we-learned"; the titles pair them correctly.
    """
    podcast = _two_source_podcast(db_session)
    listing = [
        ("the-tenth-one", "The Tenth One"),
        ("10-things-we-learned", "10 Things We Learned"),
    ]
    links = dict(listing if tenth_first else reversed(listing))
    tenth_day = _FEED_DAY - timedelta(days=30)
    feed = [
        _feed_item(
            10,
            title="The Tenth One",
            guid="urn:example:tenth",
            link="https://show.example/tenth",
            published_at=tenth_day,
        ),
        _feed_item(
            347,
            title="10 Things We Learned",
            guid="urn:example:things",
            link="https://show.example/things",
        ),
    ]

    _run(db_session, monkeypatch, podcast, _Round(links=links, now=_LATER[0]))
    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links=links, feed=feed, now=_LATER[1]),
    )
    _run(db_session, monkeypatch, podcast, _Round(feed=feed, now=_LATER[2]))

    assert_that(_rows(db_session, podcast)).is_length(2)
    _assert_one_row_per_listing_url(db_session, podcast)
    by_url = {row.episode_url: row for row in _rows(db_session, podcast)}
    assert_that(by_url).is_length(2)
    tenth = by_url[_listing_url("the-tenth-one")]
    things = by_url[_listing_url("10-things-we-learned")]
    assert_that(tenth.rss_guid).is_equal_to("urn:example:tenth")
    assert_that(tenth.published_at and tenth.published_at.date()).is_equal_to(
        tenth_day.date(),
    )
    assert_that(things.rss_guid).is_equal_to("urn:example:things")
    assert_that(things.published_at and things.published_at.date()).is_equal_to(
        _FEED_DAY.date(),
    )
    # The feed-only run reads an empty listing, which records no walk.
    assert_that(_walked_at(podcast)).is_equal_to(_LATER[1])


@pytest.mark.parametrize(
    "newest_first", [True, False], ids=["18-then-17", "17-then-18"]
)
def test_equal_number_pairs_before_an_identical_title(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    newest_first: bool,
) -> None:
    """The listing's episode 17 pairs with feed episode 17, not with 18.

    All three titles are "Protect Our Parks"; only the numbers tell them apart.
    """
    podcast = _two_source_podcast(db_session)
    seventeen = _feed_item(
        17,
        title="Protect Our Parks",
        guid="urn:example:17",
        link="https://show.example/17",
    )
    eighteen = _feed_item(
        18,
        title="Protect Our Parks",
        guid="urn:example:18",
        link="https://show.example/18",
        published_at=_FEED_DAY + timedelta(days=7),
    )
    feed = [eighteen, seventeen] if newest_first else [seventeen, eighteen]

    _run(
        db_session,
        monkeypatch,
        podcast,
        _Round(links={"17-protect-our-parks": "Protect Our Parks"}, feed=feed),
    )

    rows = {row.episode_number: row for row in _rows(db_session, podcast)}
    assert_that(sorted(rows)).is_equal_to([17, 18])
    assert_that(rows[17].episode_url).is_equal_to(
        _listing_url("17-protect-our-parks"),
    )
    assert_that(rows[17].rss_guid).is_equal_to("urn:example:17")
    assert_that(rows[17].published_at and rows[17].published_at.date()).is_equal_to(
        _FEED_DAY.date(),
    )
    assert_that(rows[18].rss_guid).is_equal_to("urn:example:18")
