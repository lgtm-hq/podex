"""Tests for the discovery-tracking columns on podcasts and episodes."""

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import DiscoverySource, Episode, Podcast


def test_podcast_stores_provider_handles(db_session: Session) -> None:
    """Provider handles and the discovery source round-trip on podcasts."""
    podcast = Podcast(
        name="Example Show",
        slug="example-show-discovery",
        rss_url="https://feeds.example.com/example-show.xml",
        spotify_id="0abc123",
        podscripts_slug="example-show",
        discovery_source=DiscoverySource.RSS,
    )
    db_session.add(podcast)
    db_session.commit()

    stored = db_session.execute(
        select(Podcast).where(Podcast.slug == "example-show-discovery"),
    ).scalar_one()
    assert_that(stored.rss_url).is_equal_to(
        "https://feeds.example.com/example-show.xml"
    )
    assert_that(stored.spotify_id).is_equal_to("0abc123")
    assert_that(stored.podscripts_slug).is_equal_to("example-show")
    assert_that(stored.discovery_source).is_equal_to(DiscoverySource.RSS)
    assert_that(stored.apple_id).is_none()
    assert_that(stored.youtube_channel_id).is_none()


def test_episode_stores_source_identifiers(db_session: Session) -> None:
    """Source-specific ids used for dedup round-trip on episodes."""
    podcast = Podcast(name="Example Show", slug="example-show-episode-ids")
    db_session.add(podcast)
    db_session.commit()

    episode = Episode(
        podcast_id=podcast.id,
        title="Pilot",
        duration_seconds=3600,
        youtube_id="dQw4w9WgXcQ",
        spotify_uri="spotify:episode:0abc123",
        rss_guid="urn:example:episode:1",
        episode_url="https://podcasts.example.com/example-show/1",
        discovery_source=DiscoverySource.PODSCRIPTS,
    )
    db_session.add(episode)
    db_session.commit()

    stored = db_session.execute(select(Episode)).scalar_one()
    assert_that(stored.duration_seconds).is_equal_to(3600)
    assert_that(stored.youtube_id).is_equal_to("dQw4w9WgXcQ")
    assert_that(stored.spotify_uri).is_equal_to("spotify:episode:0abc123")
    assert_that(stored.rss_guid).is_equal_to("urn:example:episode:1")
    assert_that(stored.discovery_source).is_equal_to(DiscoverySource.PODSCRIPTS)
