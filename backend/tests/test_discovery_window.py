"""Tests for the discovery window: known-episode paging and the ``since`` cut-off.

The orchestrator runs its real Podscripts provider against listing pages served
by ``httpx.MockTransport``; no request leaves the process.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast
from podex.services import discovery_podscripts
from podex.services.discovery import DiscoveredEpisode, published_after
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_rss import RSSDiscovery
from tests.conftest import persist_episode

_LISTING = {
    1: [10, 9],
    2: [8, 7],
    3: [6, 5],
}
_KNOWN_DATES = {
    number: datetime(2026, 1, number, tzinfo=UTC) for number in (8, 7, 6, 5)
}


def _url(number: int) -> str:
    """Return the Podscripts episode URL for ``number``."""
    return f"https://podscripts.co/podcasts/show/{number}-episode"


@pytest.fixture(name="requested_pages")
def requested_pages_fixture(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Serve ``_LISTING`` to every Podscripts client and record pages requested."""
    requested: list[int] = []

    def route(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested.append(page)
        links = "".join(
            f'<a href="/podcasts/show/{number}-episode">Episode {number}</a>'
            for number in _LISTING.get(page, [])
        )
        return httpx.Response(200, text=links)

    monkeypatch.setattr(
        discovery_podscripts,
        "build_podscripts_client",
        lambda: httpx.Client(transport=httpx.MockTransport(route)),
    )
    monkeypatch.setattr(discovery_podscripts.time, "sleep", lambda _seconds: None)
    return requested


@pytest.fixture(name="orchestrator")
def orchestrator_fixture(
    db_session: Session,
    requested_pages: list[int],
) -> Iterator[DiscoveryOrchestrator]:
    """Return an orchestrator whose Podscripts provider reads ``_LISTING``."""
    del requested_pages
    with DiscoveryOrchestrator(db_session) as orchestrator:
        yield orchestrator


def _podcast_with_known_episodes(db: Session, *, rss_url: str | None = None) -> Podcast:
    """Persist a podcast whose catalog holds the dated episodes 5 to 8."""
    podcast = Podcast(
        name="Example Show",
        slug="example-show",
        podscripts_slug="show",
        rss_url=rss_url,
    )
    for number, published_at in _KNOWN_DATES.items():
        persist_episode(
            db,
            podcast=podcast,
            title=f"Episode {number}",
            episode_number=number,
            episode_url=_url(number),
            published_at=published_at,
        )
    db.commit()
    return podcast


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        (None, [10, 9, 8, 7]),
        (datetime(2026, 1, 7, tzinfo=UTC), [10, 9, 8]),
        (datetime(2026, 1, 7, tzinfo=UTC) - timedelta(seconds=1), [10, 9, 8, 7]),
        (datetime(2026, 1, 8, tzinfo=UTC), [10, 9]),
        (datetime(2026, 2, 1, tzinfo=UTC), [10, 9]),
    ],
    ids=[
        "no-cut-off",
        "at-a-known-date",
        "just-before-a-known-date",
        "at-the-newest-known-date",
        "after-every-known-date",
    ],
)
def test_run_reads_to_the_first_known_page_and_returns_only_the_window(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    requested_pages: list[int],
    since: datetime | None,
    expected: list[int],
) -> None:
    """Paging stops at known episodes; catalog dates place episodes in the window."""
    podcast = _podcast_with_known_episodes(db_session)

    result = orchestrator.discover_for_podcast(podcast, since=since)

    assert_that(requested_pages).is_equal_to([1, 2])
    assert_that(
        [episode.episode_number for episode in result.discovered_episodes],
    ).is_equal_to(expected)
    assert_that(result.new_episodes).is_equal_to(2)
    assert_that(result.errors).is_empty()
    stored = db_session.query(Episode).filter_by(podcast_id=podcast.id).count()
    assert_that(stored).is_equal_to(6)


def test_first_run_on_a_podcast_without_episodes_reads_the_whole_listing(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    requested_pages: list[int],
) -> None:
    """A podcast with no catalog episodes reads every page, as before."""
    podcast = Podcast(name="Example Show", slug="example-show", podscripts_slug="show")
    db_session.add(podcast)
    db_session.commit()

    result = orchestrator.discover_for_podcast(podcast)

    assert_that(requested_pages).is_equal_to([1, 2, 3, 4])
    assert_that(result.new_episodes).is_equal_to(6)


@pytest.mark.parametrize(
    ("offset", "kept"),
    [(timedelta(0), False), (timedelta(seconds=1), True)],
    ids=["dated-at-the-cut-off", "dated-just-after"],
)
def test_orchestrator_drops_a_dated_episode_at_the_cut_off(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    kept: bool,
) -> None:
    """The window is strictly after ``since`` whichever provider dated the episode."""
    since = datetime(2026, 3, 1, tzinfo=UTC)
    dated = DiscoveredEpisode(
        title="Feed Only",
        published_at=since + offset,
        rss_guid="urn:example:feed-only",
        discovery_source="rss",
    )

    def feed(
        self: RSSDiscovery,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Return the dated episode whatever the window."""
        del self, podcast, since
        return [dated]

    monkeypatch.setattr(RSSDiscovery, "discover_episodes", feed)
    podcast = _podcast_with_known_episodes(db_session, rss_url="https://feeds.example")

    result = orchestrator.discover_for_podcast(podcast, since=since)

    titles = [episode.title for episode in result.discovered_episodes]
    assert_that("Feed Only" in titles).is_equal_to(kept)
    assert_that(titles).contains("Episode 10", "Episode 9")


@pytest.mark.parametrize(
    ("published_at", "since", "inside"),
    [
        (None, datetime(2026, 1, 1, tzinfo=UTC), True),
        (datetime(2026, 1, 1, tzinfo=UTC), None, True),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), False),
        (datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC), False),
        (datetime(2026, 1, 1, 0, 0, 1), datetime(2026, 1, 1, tzinfo=UTC), True),
    ],
    ids=[
        "undated-is-kept",
        "no-cut-off",
        "at-the-cut-off",
        "naive-at-the-cut-off",
        "naive-after",
    ],
)
def test_window_is_strictly_after_since(
    published_at: datetime | None,
    since: datetime | None,
    inside: bool,
) -> None:
    """Dates at the cut-off fall outside; naive dates are read as UTC."""
    assert_that(published_after(published_at, since)).is_equal_to(inside)
