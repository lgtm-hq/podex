"""Synthetic feeds, listings and discovery runs for feed identity tests.

Feeds are read by the real feed reader and listings by the real podscripts.co
provider; both are served from memory, so nothing touches the network.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast
from podex.services import discovery_orchestrator, discovery_podscripts
from podex.services.discovery import DiscoveredEpisode
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_rss import RSSDiscovery
from podex.services.feed_http import FeedHttpClient

SITE = "https://show.example/"
FEED_URL = "https://feeds.example/show"
DAY = datetime(2026, 5, 4, 6, 0, tzinfo=UTC)
MARK = 4321
"""Duration a probing record carries, to see which row it updated."""


@dataclass(frozen=True, slots=True)
class FeedItem:
    """One feed item; ``None`` leaves the element out.

    A ``published_at`` given as text is written as it is, to give a date the
    reader cannot parse.
    """

    title: str
    guid: str | None = None
    link: str | None = None
    season: str | None = None
    episode: str | None = None
    published_at: datetime | str | None = DAY
    duration: str | None = None


def two_seasons(*, guids: bool = True) -> list[FeedItem]:
    """Return season 1 episode 5 and season 2 episode 5, with their own links."""
    return [
        FeedItem(
            "The Arrival",
            guid="urn:show:s1e5" if guids else None,
            link=f"{SITE}s1e5",
            season="1",
            episode="5",
            published_at=DAY - timedelta(days=300),
        ),
        FeedItem(
            "The Departure",
            guid="urn:show:s2e5" if guids else None,
            link=f"{SITE}s2e5",
            season="2",
            episode="5",
        ),
    ]


def feed_xml(items: Sequence[FeedItem], *, site: str = SITE) -> str:
    """Return an RSS document holding ``items`` under the show's site link."""
    parts: list[str] = []
    for item in items:
        parts.append(f"<item><title>{escape(item.title)}</title>")
        optional = {
            "guid": item.guid,
            "link": item.link,
            "itunes:season": item.season,
            "itunes:episode": item.episode,
            "itunes:duration": item.duration,
        }
        for name, value in optional.items():
            if value is not None:
                parts.append(f"<{name}>{escape(value)}</{name}>")
        date = item.published_at
        if isinstance(date, datetime):
            date = date.strftime("%a, %d %b %Y %H:%M:%S GMT")
        if date is not None:
            parts.append(f"<pubDate>{escape(date)}</pubDate>")
        parts.append("</item>")
    return (
        '<?xml version="1.0"?>'
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>Feed Show</title><link>{escape(site)}</link>"
        f"{''.join(parts)}</channel></rss>"
    )


def feed_reader(xml: str) -> RSSDiscovery:
    """Return the real feed reader serving ``xml`` without network traffic."""

    async def resolve(host: str, port: int) -> list[str]:
        """Return a public address without DNS traffic."""
        del host, port
        return ["93.184.216.34"]

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(xml.encode())),
    )
    return RSSDiscovery(FeedHttpClient(resolver=resolve, transport=transport))


def listing_url(slug: str) -> str:
    """Return the listing URL the podscripts.co provider builds for ``slug``."""
    return f"https://podscripts.co/podcasts/show/{slug}"


def _listing_client(links: dict[str, str]) -> Callable[[], httpx.Client]:
    """Return a client factory serving ``links`` as the only listing page."""
    first_page = "".join(
        f'<a href="/podcasts/show/{slug}">{escape(title)}</a>'
        for slug, title in links.items()
    )

    def answer(request: httpx.Request) -> httpx.Response:
        """Answer page 1 with the links and every later page empty."""
        page = request.url.params.get("page")
        return httpx.Response(200, text=first_page if page == "1" else "")

    return lambda: httpx.Client(transport=httpx.MockTransport(answer))


def show(db: Session, *, listing: bool = False) -> Podcast:
    """Persist a podcast read from its feed, and from its listing if asked."""
    podcast = Podcast(
        name="Feed Show",
        slug="feed-show",
        rss_url=FEED_URL,
        podscripts_slug="show" if listing else None,
    )
    db.add(podcast)
    db.commit()
    return podcast


def stored(db: Session, podcast: Podcast) -> list[Episode]:
    """Return the podcast's stored episodes in creation order."""
    return list(
        db.scalars(
            select(Episode)
            .where(Episode.podcast_id == podcast.id)
            .order_by(Episode.id),
        ).all(),
    )


def store(db: Session, podcast: Podcast, **fields: object) -> Episode:
    """Persist one episode row as an earlier release would have stored it."""
    row = Episode(podcast_id=podcast.id, **fields)
    db.add(row)
    db.commit()
    return row


class RecordedFeed:
    """Feed source that reports fixed records, as the reader would."""

    name = "rss"
    is_configured = True

    def __init__(self, records: Sequence[DiscoveredEpisode]) -> None:
        self._records = list(records)

    def discover_podcast(self, identifier: str) -> None:
        """Report no podcast metadata; discovery runs never ask for it."""
        del identifier

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Return the fixed records."""
        del podcast, since
        return list(self._records)


@dataclass(frozen=True, slots=True)
class Round:
    """What one discovery run reads.

    ``items`` go through the real feed reader; ``records`` replace the feed
    reader's output when a test needs a value the reader would not emit.
    """

    items: Sequence[FeedItem] = ()
    records: Sequence[DiscoveredEpisode] | None = None
    links: dict[str, str] | None = None
    since: datetime | None = None


def discover(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    run: Round,
) -> int:
    """Run one discovery and return how many rows the podcast then has."""
    if run.records is not None:
        records = RecordedFeed(run.records)
        monkeypatch.setattr(discovery_orchestrator, "RSSDiscovery", lambda: records)
    else:
        xml = feed_xml(run.items)
        monkeypatch.setattr(
            discovery_orchestrator,
            "RSSDiscovery",
            lambda: feed_reader(xml),
        )
    monkeypatch.setattr(
        discovery_podscripts,
        "build_podscripts_client",
        _listing_client(run.links or {}),
    )
    monkeypatch.setattr(discovery_podscripts.time, "sleep", lambda _seconds: None)
    with DiscoveryOrchestrator(db) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast, since=run.since)
    assert_that(result.errors).is_empty()
    return len(stored(db, podcast))


def marked_record(title: str = "Unrelated", /, **fields: object) -> DiscoveredEpisode:
    """Return a feed record carrying ``MARK``, to see which row it updates."""
    record = DiscoveredEpisode(
        title=title,
        duration_seconds=MARK,
        discovery_source="rss",
    )
    for name, value in fields.items():
        setattr(record, name, value)
    return record


def counts(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    podcast: Podcast,
    runs: Sequence[Round],
) -> list[int]:
    """Run each round in turn and return the row count after each."""
    return [discover(db, monkeypatch, podcast, run) for run in runs]
