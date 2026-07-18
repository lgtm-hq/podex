"""Tests for the RSS/Spotify discovery providers and orchestrator flows."""

from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast
from podex.services.discovery import DiscoveredEpisode, DiscoveredPodcast
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_podscripts import PodscriptsDiscovery
from podex.services.discovery_rss import RSSDiscovery
from podex.services.discovery_spotify import SpotifyDiscovery

_FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Example Show</title>
    <description>A show about examples.</description>
    <itunes:author>Example Author</itunes:author>
    <image><url>https://cdn.example.com/cover.jpg</url></image>
    <item>
      <title>Pilot</title>
      <guid>urn:example:episode:1</guid>
      <link>https://podcasts.example.com/example-show/1</link>
      <pubDate>Fri, 03 Jan 2026 10:00:00 GMT</pubDate>
      <itunes:duration>1:02:03</itunes:duration>
      <itunes:episode>1</itunes:episode>
      <description>&lt;p&gt;The very first episode.&lt;/p&gt;</description>
    </item>
    <item>
      <title>Untimed</title>
      <guid>urn:example:episode:2</guid>
      <itunes:duration>3600</itunes:duration>
    </item>
  </channel>
</rss>
"""


class _StubProvider:
    """Minimal provider double returning canned episodes."""

    name = "rss"
    is_configured = True

    def __init__(self, episodes: list[DiscoveredEpisode]) -> None:
        self._episodes = episodes

    def discover_podcast(self, identifier: str) -> DiscoveredPodcast | None:
        """Return no podcast metadata; unused by these tests."""
        del identifier
        return None

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Return the canned episode list."""
        del podcast, since
        return self._episodes


class _BoomProvider(_StubProvider):
    """Provider double whose discovery always fails."""

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Always raise to exercise the error-capture path."""
        del podcast, since
        raise RuntimeError("feed exploded")


def test_rss_discover_podcast_parses_channel_metadata() -> None:
    """Channel metadata maps onto DiscoveredPodcast fields."""
    podcast = RSSDiscovery().discover_podcast(_FEED)

    assert_that(podcast).is_not_none()
    parsed = cast("DiscoveredPodcast", podcast)
    assert_that(parsed.name).is_equal_to("Example Show")
    assert_that(parsed.description).contains("examples")
    assert_that(parsed.discovery_source).is_equal_to("rss")


def test_rss_discover_episodes_parses_entries(db_session: Session) -> None:
    """Feed entries parse duration, guid, date, and stripped description."""
    del db_session
    stub = Podcast(name="Example Show", slug="example-show", rss_url=_FEED)

    episodes = RSSDiscovery().discover_episodes(stub)

    assert_that(episodes).is_length(2)
    pilot = episodes[0]
    assert_that(pilot.title).is_equal_to("Pilot")
    assert_that(pilot.duration_seconds).is_equal_to(3723)
    assert_that(pilot.episode_number).is_equal_to(1)
    assert_that(pilot.rss_guid).is_equal_to("urn:example:episode:1")
    assert_that(pilot.description).does_not_contain("<p>")
    assert_that(pilot.published_at).is_equal_to(
        datetime(2026, 1, 3, 10, 0, tzinfo=UTC),
    )
    assert_that(episodes[1].duration_seconds).is_equal_to(3600)


def test_rss_duration_and_slug_helpers() -> None:
    """Duration strings and slugs normalise across shapes."""
    rss = RSSDiscovery()

    assert_that(rss._parse_duration("2:30")).is_equal_to(150)
    assert_that(rss._parse_duration("90")).is_equal_to(90)
    assert_that(rss._parse_duration("nope")).is_none()
    assert_that(rss._generate_slug("The Example Show!")).is_equal_to(
        "the-example-show",
    )


def test_spotify_unconfigured_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without credentials the Spotify provider degrades gracefully."""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    spotify = SpotifyDiscovery()

    assert_that(spotify.is_configured).is_false()
    assert_that(spotify.discover_podcast("0abc123")).is_none()
    stub = Podcast(name="Example Show", slug="example-show", spotify_id="0abc123")
    assert_that(spotify.discover_episodes(stub)).is_empty()


def test_spotify_parse_episode_maps_api_fields() -> None:
    """A Spotify API item maps onto DiscoveredEpisode fields."""
    item = {
        "name": "Pilot",
        "release_date": "2026-01-03",
        "duration_ms": 3723000,
        "external_urls": {"spotify": "https://open.spotify.com/episode/0abc"},
        "uri": "spotify:episode:0abc",
        "description": "d" * 1200,
        "images": [{"url": "https://i.scdn.co/image/x"}],
    }

    episode = SpotifyDiscovery()._parse_episode(item)

    assert_that(episode).is_not_none()
    parsed = cast("DiscoveredEpisode", episode)
    assert_that(parsed.duration_seconds).is_equal_to(3723)
    assert_that(parsed.spotify_uri).is_equal_to("spotify:episode:0abc")
    assert_that(parsed.published_at).is_equal_to(
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert_that(len(parsed.description or "")).is_less_than_or_equal_to(1003)


def test_orchestrator_merges_sources_and_records_errors(
    db_session: Session,
) -> None:
    """Discovery fans out, dedups across sources, and captures errors."""
    podcast = Podcast(
        name="Example Show",
        slug="example-show-orch",
        rss_url="ignored",
        podscripts_slug="example-show",
    )
    db_session.add(podcast)
    db_session.commit()

    shared_date = datetime(2026, 1, 3, tzinfo=UTC)
    rss_ep = DiscoveredEpisode(
        title="Pilot",
        published_at=shared_date,
        rss_guid="urn:example:episode:1",
        discovery_source="rss",
    )
    pod_ep = DiscoveredEpisode(
        title="Pilot",
        published_at=shared_date,
        has_transcript=True,
        discovery_source="podscripts",
    )

    orchestrator = DiscoveryOrchestrator(db_session)
    orchestrator._sources = {
        "rss": _StubProvider([rss_ep]),
        "podscripts": _BoomProvider([pod_ep]),
        "spotify": _StubProvider([]),
    }
    result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.new_episodes).is_equal_to(1)
    assert_that(result.errors).is_length(1)
    assert_that(result.errors[0]).contains("podscripts")

    # Second run with both sources healthy dedups into the same episode.
    orchestrator._sources["podscripts"] = _StubProvider([pod_ep])
    second = orchestrator.discover_for_podcast(podcast)
    assert_that(second.new_episodes).is_equal_to(0)
    assert_that(
        db_session.query(Episode).filter_by(podcast_id=podcast.id).count(),
    ).is_equal_to(1)


def _podscripts_source_with_html(pages: dict[int, str]) -> PodscriptsDiscovery:
    """Return a podscripts provider whose HTTP client serves canned pages."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Serve the canned page for the requested page number."""
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, text=pages.get(page, ""))

    source = PodscriptsDiscovery(delay=0)
    source.client = httpx.Client(transport=httpx.MockTransport(handler))
    return source


def test_podscripts_discover_podcast_parses_page() -> None:
    """Podcast metadata comes from title/h1 and meta tags."""
    html = (
        "<html><head><title>Example Show - Podscripts</title>"
        '<meta name="description" content="A show about examples.">'
        '<meta property="og:image" content="https://cdn.example.com/c.jpg">'
        "</head><body><h1>Example Show</h1></body></html>"
    )
    source = _podscripts_source_with_html({1: html})
    try:
        podcast = source.discover_podcast("example-show")
    finally:
        source.close()

    assert_that(podcast).is_not_none()
    parsed = cast("DiscoveredPodcast", podcast)
    assert_that(parsed.name).is_equal_to("Example Show")
    assert_that(parsed.description).is_equal_to("A show about examples.")
    assert_that(parsed.cover_url).is_equal_to("https://cdn.example.com/c.jpg")


def test_podscripts_discover_episodes_paginates_and_dedups(
    db_session: Session,
) -> None:
    """Episode links parse across pages until an empty page stops the walk."""
    del db_session
    page1 = (
        '<a href="/podcasts/example-show/42-pilot">Pilot</a>'
        '<a href="/podcasts/example-show/42-pilot">Pilot dup</a>'
        '<a href="/podcasts/example-show">index link ignored</a>'
    )
    source = _podscripts_source_with_html({1: page1})
    stub = Podcast(
        name="Example Show",
        slug="example-show",
        podscripts_slug="example-show",
    )
    try:
        episodes = source.discover_episodes(stub)
        empty = source.discover_episodes(
            Podcast(name="No Slug", slug="no-slug"),
        )
    finally:
        source.close()

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].has_transcript).is_true()
    assert_that(episodes[0].discovery_source).is_equal_to("podscripts")
    assert_that(empty).is_empty()


class _FakeSpotify:
    """Stand-in for the spotipy client covering show + episodes calls."""

    def show(self, spotify_id: str, market: str) -> dict[str, object]:
        """Return canned show metadata."""
        del spotify_id, market
        return {
            "name": "Example Show",
            "description": "A show about examples.",
            "publisher": "Example Author",
            "images": [
                {"url": "https://i.scdn.co/small", "height": 64},
                {"url": "https://i.scdn.co/large", "height": 640},
            ],
        }

    def show_episodes(
        self,
        spotify_id: str,
        limit: int,
        offset: int,
        market: str,
    ) -> dict[str, object] | None:
        """Return one canned page of episodes then stop."""
        del spotify_id, limit, market
        if offset:
            return {"items": [], "next": None}
        return {
            "items": [
                {
                    "name": "Pilot",
                    "release_date": "2026-01-03",
                    "duration_ms": 60000,
                    "uri": "spotify:episode:0abc",
                    "external_urls": {},
                },
            ],
            "next": None,
        }


def test_spotify_configured_paths_use_client() -> None:
    """With a client injected, show and episode discovery map API payloads."""
    spotify = SpotifyDiscovery(
        client_id="id",
        client_secret="secret",  # nosec B106 - fake test credential
    )
    spotify._spotify = _FakeSpotify()

    podcast = spotify.discover_podcast("0abc123")
    assert_that(podcast).is_not_none()
    parsed = cast("DiscoveredPodcast", podcast)
    assert_that(parsed.name).is_equal_to("Example Show")
    assert_that(parsed.cover_url).is_equal_to("https://i.scdn.co/large")

    stub = Podcast(
        name="Example Show",
        slug="example-show",
        spotify_id="0abc123",
    )
    episodes = spotify.discover_episodes(stub)
    assert_that(episodes).is_length(1)
    assert_that(episodes[0].spotify_uri).is_equal_to("spotify:episode:0abc")


def test_orchestrator_merge_prefers_transcript_source_and_fills_gaps() -> None:
    """Merging keeps the transcript source's title and unions metadata."""
    base = DiscoveredEpisode(
        title="Pilot (raw feed)",
        rss_guid="urn:example:episode:1",
        discovery_source="rss",
    )
    other = DiscoveredEpisode(
        title="Pilot",
        published_at=datetime(2026, 1, 3, tzinfo=UTC),
        duration_seconds=3600,
        episode_number=42,
        description="The very first episode.",
        episode_url="https://podcasts.example.com/example-show/1",
        thumbnail_url="https://cdn.example.com/t.jpg",
        youtube_id="dQw4w9WgXcQ",
        spotify_uri="spotify:episode:0abc",
        apple_id="123",
        has_transcript=True,
        discovery_source="podscripts",
    )

    merged = DiscoveryOrchestrator._merge_episodes(
        cast("DiscoveryOrchestrator", object()),
        [base, other],
    )

    assert_that(merged.title).is_equal_to("Pilot")
    assert_that(merged.has_transcript).is_true()
    assert_that(merged.duration_seconds).is_equal_to(3600)
    assert_that(merged.episode_number).is_equal_to(42)
    assert_that(merged.youtube_id).is_equal_to("dQw4w9WgXcQ")
    assert_that(merged.spotify_uri).is_equal_to("spotify:episode:0abc")
    assert_that(merged.apple_id).is_equal_to("123")
    assert_that(merged.rss_guid).is_equal_to("urn:example:episode:1")


def test_orchestrator_update_fills_missing_episode_fields(
    db_session: Session,
) -> None:
    """Re-discovery fills gaps on an existing episode without overwriting."""
    podcast = Podcast(name="Example Show", slug="example-show-update")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(
        podcast_id=podcast.id,
        title="Pilot",
        rss_guid="urn:example:episode:1",
    )
    db_session.add(episode)
    db_session.commit()

    richer = DiscoveredEpisode(
        title="Pilot",
        rss_guid="urn:example:episode:1",
        published_at=datetime(2026, 1, 3, tzinfo=UTC),
        duration_seconds=3600,
        episode_url="https://podcasts.example.com/example-show/1",
        thumbnail_url="https://cdn.example.com/t.jpg",
        youtube_id="dQw4w9WgXcQ",
        spotify_uri="spotify:episode:0abc",
        apple_id="123",
        discovery_source="rss",
    )
    orchestrator = DiscoveryOrchestrator(db_session)
    new_count, updated_count = orchestrator._upsert_episodes(podcast, [richer])

    assert_that(new_count).is_equal_to(0)
    assert_that(updated_count).is_equal_to(1)
    db_session.refresh(episode)
    assert_that(episode.duration_seconds).is_equal_to(3600)
    assert_that(episode.youtube_id).is_equal_to("dQw4w9WgXcQ")
    assert_that(episode.spotify_uri).is_equal_to("spotify:episode:0abc")
