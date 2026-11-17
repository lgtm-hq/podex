"""Tests for idempotent episode discovery persistence."""

from datetime import datetime
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
