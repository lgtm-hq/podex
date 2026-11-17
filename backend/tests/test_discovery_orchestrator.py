"""Tests for idempotent episode discovery persistence."""

from datetime import UTC, datetime
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


_HOMEPAGE = "https://show.example/"


def test_feed_entries_sharing_a_link_with_different_numbers_stay_apart(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared link does not merge entries whose episode numbers differ."""
    _serve_feed(
        monkeypatch,
        [
            DiscoveredEpisode(
                title=f"Protect Our Parks {series}",
                episode_number=number,
                episode_url=_HOMEPAGE,
                rss_guid=f"urn:example:{number}",
                discovery_source="rss",
            )
            for series, number in ((18, 2581), (17, 2580))
        ],
    )
    podcast = _feed_podcast(db_session)

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(2)
    numbers = db_session.scalars(
        select(Episode.episode_number).where(Episode.podcast_id == podcast.id),
    ).all()
    assert_that(sorted(number for number in numbers if number is not None)).is_equal_to(
        [2580, 2581],
    )


def test_catalog_match_skips_rows_whose_number_differs(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared link never matches a catalog row numbered differently.

    The discovered episode matches the row with its own number instead, even
    though that row's title and link differ.
    """
    podcast = _feed_podcast(db_session)
    older = persist_episode(
        db_session,
        podcast=podcast,
        title="Protect Our Parks 17",
        episode_number=2580,
        episode_url=_HOMEPAGE,
    )
    same_number = persist_episode(
        db_session,
        podcast=podcast,
        title="#2581 - Protect Our Parks 18",
        episode_number=2581,
        episode_url="https://podscripts.co/podcasts/show/2581-protect-our-parks-18",
    )
    db_session.commit()
    _serve_feed(
        monkeypatch,
        [
            DiscoveredEpisode(
                title="Protect Our Parks 18",
                episode_number=2581,
                episode_url=_HOMEPAGE,
                rss_guid="urn:example:2581",
                discovery_source="rss",
            ),
        ],
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(0)
    assert_that(result.updated_episodes).is_equal_to(1)
    db_session.refresh(older)
    db_session.refresh(same_number)
    assert_that(older.rss_guid).is_none()
    assert_that(same_number.rss_guid).is_equal_to("urn:example:2581")


def test_catalog_row_numbered_differently_is_not_updated_by_a_new_episode(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new numbered episode is created, not folded into a row with another number."""
    podcast = _feed_podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="Protect Our Parks 17",
        episode_number=2580,
        episode_url=_HOMEPAGE,
    )
    db_session.commit()
    _serve_feed(
        monkeypatch,
        [
            DiscoveredEpisode(
                title="Protect Our Parks 18",
                episode_number=2581,
                episode_url=_HOMEPAGE,
                rss_guid="urn:example:2581",
                discovery_source="rss",
            ),
        ],
    )

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(1)
    assert_that(db_session.query(Episode).count()).is_equal_to(2)


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
    guid: str = "urn:example:the-return",
    link: str | None = _FEED_LINK,
) -> DiscoveredEpisode:
    """Return one dated feed entry."""
    return DiscoveredEpisode(
        title=title,
        episode_number=number,
        published_at=_FEED_DAY,
        episode_url=link,
        rss_guid=guid,
        discovery_source="rss",
    )


def _run(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    *,
    links: dict[str, str],
    feed: list[DiscoveredEpisode],
) -> None:
    """Run one discovery over the given listing links and feed entries."""
    _serve_listing(monkeypatch, links)
    _serve_feed(monkeypatch, feed)
    with DiscoveryOrchestrator(db, clock=lambda: _NOW) as orchestrator:
        orchestrator.discover_for_podcast(podcast)


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
    """Episode number 0 means no number, so it joins nothing."""
    titles = ["Trailer", "Bonus: Live Q&A", "Announcement", "Holiday Special"]
    _serve_feed(
        monkeypatch,
        [
            _feed_item(
                0,
                title=title,
                guid=f"urn:example:{index}",
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
        links={slug: title},
        feed=[_feed_item(feed_number, title=title)],
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
        links=links if listing_first else {},
        feed=[] if listing_first else feed,
    )
    _run(db_session, monkeypatch, podcast, links=links, feed=feed)

    assert_that(_rows(db_session, podcast)).is_length(1)


@pytest.mark.parametrize("stored_number", [None, 0], ids=["null", "zero"])
def test_url_match_accepts_rows_without_a_number(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    stored_number: int | None,
) -> None:
    """A numbered record finds an unnumbered row with its URL."""
    podcast = _feed_podcast(db_session)
    persist_episode(
        db_session,
        podcast=podcast,
        title="The Return",
        episode_number=stored_number,
        episode_url=_FEED_LINK,
    )
    db_session.commit()
    _serve_feed(monkeypatch, [_feed_item(5)])

    with DiscoveryOrchestrator(db_session) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(0)
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
            links=links,
            feed=[_feed_item(number)],
        )

    listing_url = _listing_url("the-return")
    rows = _rows(db_session, podcast)
    assert_that(rows).is_length(1)
    assert_that([row.episode_url for row in rows]).is_equal_to([listing_url])
    assert_that(podcast.podscripts_listing_walked_at).is_not_none()
