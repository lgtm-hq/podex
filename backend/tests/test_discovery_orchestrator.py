"""Tests for idempotent episode discovery persistence."""

from assertpy import assert_that
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import DiscoverySource, Episode, Podcast
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_podscripts import PodscriptsDiscovery


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
        created = orchestrator._upsert_episodes(podcast, [discovered])
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
        episodes = orchestrator._deduplicate_episodes([first, repeated])

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].episode_url).is_equal_to(first.episode_url)


def test_podscripts_episode_comment_link_falls_back_to_url_title() -> None:
    """Verify comment-count link text cannot become an episode title."""
    link = BeautifulSoup(
        '<a href="/podcasts/example-show/42-pilot">0comments</a>',
        "html.parser",
    ).find("a")
    assert link is not None

    source = PodscriptsDiscovery(delay=0)
    try:
        episode = source._parse_episode_link(
            link,
            "/podcasts/example-show/42-pilot",
            "example-show",
        )
    finally:
        source.close()

    assert_that(episode).is_not_none()
    assert episode is not None
    assert_that(episode.title).is_equal_to("David Paulides")
    assert_that(episode.episode_number).is_equal_to(2502)


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
    new_count, updated_count = orchestrator._upsert_episodes(
        podcast,
        [discovered],
    )

    assert_that(new_count).is_equal_to(1)
    assert_that(updated_count).is_equal_to(0)
    stored = db_session.execute(select(Episode)).scalar_one()
    assert_that(stored.rss_guid).is_equal_to("urn:example:episode:1")
    assert_that(stored.discovery_source).is_equal_to(DiscoverySource.RSS)
