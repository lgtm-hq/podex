"""Tests for incremental discovery: the listing cursor and the ``since`` window.

The orchestrator runs its real Podscripts provider against a listing served by
``httpx.MockTransport``; no request leaves the process. The clock is fixed so
the complete-walk record and the full-walk interval are deterministic.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import httpx
import pytest
from assertpy import assert_that
from pydantic import ValidationError
from sqlalchemy import event, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import (
    Episode,
    IngestionRun,
    IngestionRunStatus,
    Podcast,
    PodcastStatus,
)
from podex.services import discovery_orchestrator, discovery_podscripts
from podex.services.discovery import (
    DiscoveredEpisode,
    EpisodeListing,
    ListingCursor,
    as_utc,
    published_in_window,
)
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_rss import RSSDiscovery
from podex.services.ops_podcast_commands import (
    OpsPodcastSourceInputData,
    UpdateOpsPodcastInputData,
    update_ops_podcast,
)
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    run_due_episode_discovery_work,
)
from tests.conftest import persist_episode

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_INTERVAL = timedelta(hours=168)


def _slug(number: int) -> str:
    """Return the listing slug of a numbered episode."""
    return f"{number}-episode"


def _url(slug: str, show: str = "show") -> str:
    """Return the listing URL the provider builds for ``slug``."""
    return f"https://podscripts.co/podcasts/{show}/{slug}"


def _numbered_pages(*pages: tuple[int, ...]) -> dict[int, list[str]]:
    """Return listing pages of numbered episode slugs, page 1 first."""
    return {
        index: [_slug(number) for number in numbers]
        for index, numbers in enumerate(pages, start=1)
    }


@dataclass
class _Site:
    """Listing pages served to every Podscripts client, and the pages asked for."""

    pages: dict[int, list[str]] = field(
        default_factory=lambda: _numbered_pages((12, 11), (10, 9), (8, 7), (6, 5)),
    )
    failing: dict[int, int] = field(default_factory=dict)
    requested: list[int] = field(default_factory=list)
    on_request: list[Callable[[], None]] = field(default_factory=list)

    def route(self, request: httpx.Request) -> httpx.Response:
        """Serve the requested page, or its scripted failure status."""
        page = int(request.url.params["page"])
        self.requested.append(page)
        for hook in self.on_request:
            hook()
        if page in self.failing:
            return httpx.Response(self.failing[page])
        links = "".join(
            f'<a href="/podcasts/show/{slug}">{slug}</a>'
            for slug in self.pages.get(page, [])
        )
        return httpx.Response(200, text=links)


@pytest.fixture(name="site")
def site_fixture(monkeypatch: pytest.MonkeyPatch) -> _Site:
    """Route every Podscripts client built from here on to one fake listing."""
    site = _Site()
    monkeypatch.setattr(
        discovery_podscripts,
        "build_podscripts_client",
        lambda: httpx.Client(transport=httpx.MockTransport(site.route)),
    )
    monkeypatch.setattr(discovery_podscripts.time, "sleep", lambda _seconds: None)
    return site


@pytest.fixture(name="orchestrator")
def orchestrator_fixture(
    db_session: Session,
    site: _Site,
) -> Iterator[DiscoveryOrchestrator]:
    """Return an orchestrator over the fake listing with a fixed clock."""
    del site
    with DiscoveryOrchestrator(db_session, clock=lambda: _NOW) as orchestrator:
        yield orchestrator


def _podcast(
    db: Session,
    *,
    walked_at: datetime | None = None,
    slug: str = "show",
) -> Podcast:
    """Persist a podcast listed on Podscripts under ``slug``."""
    podcast = Podcast(
        name=f"Example {slug}",
        slug=f"example-{slug}",
        podscripts_slug=slug,
        podscripts_listing_walked_at=walked_at,
    )
    db.add(podcast)
    db.commit()
    return podcast


def _store(
    db: Session,
    podcast: Podcast,
    number: int | None,
    *,
    url: str | None,
    published_at: datetime | None = None,
) -> None:
    """Persist one catalog episode for ``podcast``."""
    persist_episode(
        db,
        podcast=podcast,
        title=f"Stored {number if number is not None else url}",
        episode_number=number,
        episode_url=url,
        published_at=published_at,
    )
    db.commit()


def _stored_urls(db: Session, podcast: Podcast) -> set[str | None]:
    """Return the episode URLs stored for ``podcast``."""
    return set(
        db.scalars(
            select(Episode.episode_url).where(Episode.podcast_id == podcast.id),
        ).all(),
    )


def _walked_at(podcast: Podcast) -> datetime | None:
    """Return the podcast's complete-walk record in UTC."""
    walked_at = podcast.podscripts_listing_walked_at
    return as_utc(walked_at) if walked_at is not None else None


def test_complete_walk_is_recorded_and_lets_the_next_run_stop_early(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A first run reads the whole listing; once recorded, known pages end paging."""
    podcast = _podcast(db_session)

    first = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5])
    assert_that(first.new_episodes).is_equal_to(8)
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)

    site.requested.clear()
    site.pages = _numbered_pages((13, 12), (11, 10), (9, 8), (7, 6), (5,))
    second = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2])
    assert_that(
        [episode.episode_number for episode in second.discovered_episodes],
    ).is_equal_to([13, 12, 11, 10])
    assert_that(second.new_episodes).is_equal_to(1)
    assert_that(second.errors).is_empty()


def test_failed_page_is_a_run_error_and_the_next_run_walks_in_full(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A failed page fails the run and records no walk, so nothing is skipped later."""
    podcast = _podcast(db_session)
    site.failing = {3: 404}

    first = orchestrator.discover_for_podcast(podcast)

    assert_that(first.errors).is_equal_to(
        ["podscripts: HTTPStatusError (HTTP 404); request_failed"],
    )
    assert_that(_walked_at(podcast)).is_none()
    assert_that(_stored_urls(db_session, podcast)).is_equal_to(
        {_url(_slug(number)) for number in (12, 11, 10, 9)},
    )

    site.failing = {}
    site.requested.clear()
    second = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5])
    assert_that(second.errors).is_empty()
    assert_that(second.new_episodes).is_equal_to(4)
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)


def test_failed_page_clears_an_existing_walk_record(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A gap left by a failed page is filled by the next run, not skipped."""
    podcast = _podcast(db_session, walked_at=_NOW - timedelta(days=1))
    for number in range(12, 4, -1):
        _store(db_session, podcast, number, url=_url(_slug(number)))
    site.pages = _numbered_pages(
        (18, 17),
        (16, 15),
        (14, 13),
        (12, 11),
        (10, 9),
        (8, 7),
        (6, 5),
    )
    site.failing = {3: 404}

    first = orchestrator.discover_for_podcast(podcast)

    assert_that(first.errors).is_length(1)
    assert_that(_walked_at(podcast)).is_none()

    site.failing = {}
    site.requested.clear()
    orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5, 6, 7, 8])
    assert_that(_stored_urls(db_session, podcast)).contains(
        _url(_slug(14)),
        _url(_slug(13)),
    )
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)


def test_walk_is_not_recorded_when_storing_its_episodes_fails(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete walk whose episodes were not all stored is not recorded."""
    podcast = _podcast(db_session)

    def refuse(
        self: DiscoveryOrchestrator,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> NoReturn:
        """Fail to store any episode."""
        del self, podcast, discovered
        raise RuntimeError("storage failed")

    monkeypatch.setattr(DiscoveryOrchestrator, "_create_episode", refuse)

    with pytest.raises(RuntimeError, match="storage failed"):
        orchestrator.discover_for_podcast(podcast)

    assert_that(podcast.podscripts_listing_walked_at).is_none()


def test_scheduled_run_whose_storing_fails_leaves_no_skippable_gap(
    db_session: Session,
    site: _Site,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that stores part of the new episodes and fails clears the record."""
    podcast = _podcast(db_session, walked_at=datetime.now(UTC) - timedelta(days=1))
    podcast.status = PodcastStatus.ACTIVE
    for number in range(12, 4, -1):
        _store(db_session, podcast, number, url=_url(_slug(number)))
    site.pages = _numbered_pages(
        (18, 17, 16),
        (15, 14, 13),
        (12, 11, 10),
        (9, 8, 7),
        (6, 5),
    )
    plan_recurring_discovery_work(
        db=db_session,
        interval_minutes=60,
        now=datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
    )
    db_session.commit()
    create = vars(DiscoveryOrchestrator)["_create_episode"]
    created: list[DiscoveredEpisode] = []

    def fail_on_fourth(
        self: DiscoveryOrchestrator,
        podcast: Podcast,
        discovered: DiscoveredEpisode,
    ) -> Episode:
        """Store three new episodes, then fail."""
        created.append(discovered)
        if len(created) == 4:
            raise RuntimeError("storage failed")
        episode: Episode = create(self, podcast, discovered)
        return episode

    monkeypatch.setattr(DiscoveryOrchestrator, "_create_episode", fail_on_fourth)

    run_due_episode_discovery_work(db=db_session)
    db_session.commit()

    assert_that(db_session.query(IngestionRun).one().status).is_equal_to(
        IngestionRunStatus.FAILED,
    )
    assert_that(_stored_urls(db_session, podcast)).contains(
        *(_url(_slug(number)) for number in (18, 17, 16)),
    )
    assert_that(_walked_at(podcast)).is_none()

    monkeypatch.setattr(DiscoveryOrchestrator, "_create_episode", create)
    site.requested.clear()
    with DiscoveryOrchestrator(db_session) as orchestrator:
        orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5, 6])
    assert_that(_stored_urls(db_session, podcast)).contains(
        *(_url(_slug(number)) for number in (15, 14, 13)),
    )


def test_empty_first_page_records_no_walk_over_a_known_catalog(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A listing that reads as empty while the catalog has episodes is not trusted."""
    podcast = _podcast(db_session)
    _store(db_session, podcast, 1, url="https://feeds.example/1")
    site.pages = {}

    result = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1])
    assert_that(result.errors).is_empty()
    assert_that(_walked_at(podcast)).is_none()


def test_walk_shorter_than_the_catalogs_listing_is_not_recorded(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A soft-empty page that cuts the listing short leaves the walk unrecorded."""
    podcast = _podcast(db_session)
    for number in range(12, 4, -1):
        _store(db_session, podcast, number, url=_url(_slug(number)))
    site.pages = _numbered_pages((12, 11), (10, 9))

    orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3])
    assert_that(_walked_at(podcast)).is_none()


def test_walk_record_in_the_future_counts_as_absent(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """A record dated after now never turns the cursor on."""
    podcast = _podcast(db_session, walked_at=_NOW + timedelta(days=1))
    for number in range(12, 4, -1):
        _store(db_session, podcast, number, url=_url(_slug(number)))

    orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5])
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)


def test_walk_is_recorded_at_its_start_not_its_end(
    db_session: Session,
    site: _Site,
) -> None:
    """The record holds the time the walk began, so nothing added during it is lost."""
    ticks = [_NOW]

    def advance() -> None:
        """Move the clock forward a minute per page request."""
        ticks[0] += timedelta(minutes=1)

    site.on_request.append(advance)
    podcast = _podcast(db_session)

    with DiscoveryOrchestrator(db_session, clock=lambda: ticks[0]) as orchestrator:
        orchestrator.discover_for_podcast(podcast)

    assert_that(ticks[0]).is_greater_than(_NOW)
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)


def test_number_only_catalog_matches_do_not_end_paging(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """Rows known only by number (another source's URL) leave the listing unread."""
    podcast = _podcast(db_session, walked_at=_NOW - timedelta(days=1))
    for number in (12, 11, 10, 9):
        _store(db_session, podcast, number, url=f"https://feeds.example/{number}")

    result = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5])
    assert_that(result.new_episodes).is_equal_to(4)


@pytest.mark.parametrize(
    ("age", "late_found"),
    [(_INTERVAL - timedelta(hours=1), False), (_INTERVAL, True)],
    ids=["recent-walk-stops-early", "walk-at-the-interval-reads-all"],
)
def test_periodic_full_walk_finds_episodes_inserted_deep_in_the_listing(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
    age: timedelta,
    late_found: bool,
) -> None:
    """Once the last complete walk is an interval old, the whole listing is read."""
    walked_at = _NOW - age
    podcast = _podcast(db_session, walked_at=walked_at)
    for number in range(12, 4, -1):
        _store(db_session, podcast, number, url=_url(_slug(number)))
    site.pages[3] = [_slug(8), "late-transcript", _slug(7)]

    orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5] if late_found else [1, 2])
    assert_that(
        _url("late-transcript") in _stored_urls(db_session, podcast)
    ).is_equal_to(
        late_found,
    )
    assert_that(_walked_at(podcast)).is_equal_to(_NOW if late_found else walked_at)


def test_cursor_holds_only_this_podcasts_listing_urls(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """Another podcast's rows never make this podcast's listing look known."""
    other = _podcast(db_session, slug="other")
    for number in range(12, 4, -1):
        _store(db_session, other, number, url=_url(_slug(number)))
    podcast = _podcast(db_session, walked_at=_NOW - timedelta(days=1))

    result = orchestrator.discover_for_podcast(podcast)

    assert_that(site.requested).is_equal_to([1, 2, 3, 4, 5])
    assert_that(result.new_episodes).is_equal_to(8)


@pytest.mark.parametrize(
    ("since", "expected"),
    [
        (None, [10, 9, 8, 7]),
        (datetime(2026, 1, 7, tzinfo=UTC), [10, 9, 8, 7]),
        (datetime(2026, 1, 7, tzinfo=UTC) + timedelta(seconds=1), [10, 9, 8]),
        (datetime(2026, 1, 8, tzinfo=UTC), [10, 9, 8]),
        (datetime(2026, 2, 1, tzinfo=UTC), [10, 9]),
    ],
    ids=[
        "no-cut-off",
        "at-a-known-date",
        "just-after-a-known-date",
        "at-the-newest-known-date",
        "after-every-known-date",
    ],
)
def test_since_window_is_inclusive_and_uses_catalog_dates(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
    since: datetime | None,
    expected: list[int],
) -> None:
    """Undated listing episodes take the date of the catalog row they match."""
    podcast = _podcast(db_session, walked_at=_NOW - timedelta(days=1))
    for number in (8, 7, 6, 5):
        _store(
            db_session,
            podcast,
            number,
            url=_url(_slug(number)),
            published_at=datetime(2026, 1, number, tzinfo=UTC),
        )
    site.pages = _numbered_pages((10, 9), (8, 7), (6, 5))

    result = orchestrator.discover_for_podcast(podcast, since=since)

    assert_that(site.requested).is_equal_to([1, 2])
    assert_that(
        [episode.episode_number for episode in result.discovered_episodes],
    ).is_equal_to(expected)
    assert_that(result.new_episodes).is_equal_to(2)


@pytest.mark.parametrize(
    ("offset", "kept"),
    [(timedelta(0), True), (-timedelta(seconds=1), False)],
    ids=["dated-at-the-cut-off", "dated-just-before"],
)
def test_orchestrator_window_keeps_an_episode_dated_at_the_cut_off(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    monkeypatch: pytest.MonkeyPatch,
    offset: timedelta,
    kept: bool,
) -> None:
    """The window is at or after ``since`` whichever provider dated the episode."""
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
    podcast = Podcast(
        name="Example Show",
        slug="example-show",
        rss_url="https://feeds.example/show",
    )
    db_session.add(podcast)
    db_session.commit()

    result = orchestrator.discover_for_podcast(podcast, since=since)

    titles = [episode.title for episode in result.discovered_episodes]
    assert_that(titles).is_equal_to(["Feed Only"] if kept else [])
    assert_that(result.new_episodes).is_equal_to(1 if kept else 0)


def test_rows_outside_the_window_are_still_enriched(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
) -> None:
    """An older catalog row matched by number gets its missing listing URL."""
    podcast = _podcast(db_session)
    _store(
        db_session,
        podcast,
        7,
        url=None,
        published_at=datetime(2026, 1, 7, tzinfo=UTC),
    )

    result = orchestrator.discover_for_podcast(
        podcast,
        since=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert_that(_url(_slug(7)) in _stored_urls(db_session, podcast)).is_true()
    assert_that(result.updated_episodes).is_equal_to(1)
    assert_that(
        [episode.episode_number for episode in result.discovered_episodes],
    ).does_not_contain(7)


def test_each_episode_is_matched_against_the_catalog_once(
    db_session: Session,
    orchestrator: DiscoveryOrchestrator,
    site: _Site,
) -> None:
    """With a window, the catalog match is looked up once per episode, not twice."""
    podcast = _podcast(db_session)
    site.pages = _numbered_pages((2, 1))
    statements: list[str] = []

    def record(
        conn: Connection,
        cursor: object,
        statement: str,
        *_rest: object,
    ) -> None:
        """Record each per-episode catalog lookup."""
        del conn, cursor
        if (
            statement.lstrip().upper().startswith("SELECT")
            and "FROM episodes" in statement
            and " LIMIT " in statement
        ):
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        orchestrator.discover_for_podcast(
            podcast,
            since=datetime(2026, 1, 1, tzinfo=UTC),
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # Two unknown episodes, each tried by listing URL and then by number.
    assert_that(statements).is_length(4)


class _CursorDouble:
    """Listing provider that is not the Podscripts class, with the capability."""

    name = "podscripts"
    is_configured = True

    def __init__(self) -> None:
        self.cursors: list[ListingCursor | None] = []
        self.episode_calls = 0

    def discover_podcast(self, identifier: str) -> None:
        """Return no podcast; unused here."""
        del identifier

    def discover_episodes(
        self,
        podcast: Podcast,
        since: datetime | None = None,
    ) -> list[DiscoveredEpisode]:
        """Count calls; the capability should be used instead."""
        del podcast, since
        self.episode_calls += 1
        return []

    def discover_listing(
        self,
        podcast: Podcast,
        *,
        cursor: ListingCursor | None = None,
    ) -> EpisodeListing:
        """Record the cursor and report a complete walk of one episode."""
        del podcast
        self.cursors.append(cursor)
        return EpisodeListing(
            episodes=[
                DiscoveredEpisode(
                    title="Episode 12",
                    episode_number=12,
                    episode_url=_url(_slug(12)),
                ),
            ],
            complete=True,
        )

    def listing_url_prefix(self, podcast: Podcast) -> str | None:
        """Return the listing URL prefix the fake listing uses."""
        del podcast
        return _url("")

    def close(self) -> None:
        """Release nothing."""


def test_listing_capability_is_found_through_the_protocol(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider offering ``discover_listing`` gets the cursor, whatever its class."""
    double = _CursorDouble()
    monkeypatch.setattr(discovery_orchestrator, "PodscriptsDiscovery", lambda: double)
    podcast = _podcast(db_session, walked_at=_NOW - timedelta(days=1))
    _store(db_session, podcast, 12, url=_url(_slug(12)))

    with DiscoveryOrchestrator(db_session, clock=lambda: _NOW) as orchestrator:
        result = orchestrator.discover_for_podcast(podcast)

    assert_that(result.errors).is_empty()
    assert_that(double.episode_calls).is_zero()
    assert_that(double.cursors).is_equal_to(
        [ListingCursor(known_episode_urls=frozenset({_url(_slug(12))}))],
    )
    assert_that(_walked_at(podcast)).is_equal_to(_NOW)


@pytest.mark.parametrize(
    ("new_slug", "cleared"),
    [("renamed-show", True), ("show", False)],
    ids=["slug-changed", "slug-unchanged"],
)
def test_changing_the_podscripts_slug_clears_the_walk_record(
    db_session: Session,
    new_slug: str,
    cleared: bool,
) -> None:
    """A complete walk of one listing says nothing about another listing."""
    podcast = _podcast(db_session, walked_at=_NOW)

    update_ops_podcast(
        db=db_session,
        podcast_id=podcast.id,
        payload=UpdateOpsPodcastInputData(
            source_fields=frozenset({"podscripts_slug"}),
            sources=OpsPodcastSourceInputData(podscripts_slug=new_slug),
        ),
    )

    assert_that(_walked_at(podcast)).is_equal_to(None if cleared else _NOW)


def test_full_walk_interval_defaults_to_a_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented default is 168 hours."""
    monkeypatch.delenv(
        "PODEX_DISCOVERY__PODSCRIPTS_FULL_WALK_INTERVAL_HOURS",
        raising=False,
    )

    settings = Settings(_env_file=None)

    assert_that(settings.discovery.podscripts_full_walk_interval_hours).is_equal_to(
        168,
    )


@pytest.mark.parametrize("hours", ["0", "-1"])
def test_full_walk_interval_must_be_at_least_an_hour(
    monkeypatch: pytest.MonkeyPatch,
    hours: str,
) -> None:
    """An interval below one hour is rejected at start-up."""
    monkeypatch.setenv("PODEX_DISCOVERY__PODSCRIPTS_FULL_WALK_INTERVAL_HOURS", hours)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("published_at", "since", "inside"),
    [
        (None, datetime(2026, 1, 1, tzinfo=UTC), True),
        (datetime(2026, 1, 1, tzinfo=UTC), None, True),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), True),
        (datetime(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC), True),
        (
            datetime(2025, 12, 31, 23, 59, 59),
            datetime(2026, 1, 1, tzinfo=UTC),
            False,
        ),
    ],
    ids=[
        "undated-is-kept",
        "no-cut-off",
        "at-the-cut-off",
        "naive-at-the-cut-off",
        "naive-just-before",
    ],
)
def test_window_is_at_or_after_since(
    published_at: datetime | None,
    since: datetime | None,
    inside: bool,
) -> None:
    """Dates at the cut-off fall inside; naive dates are read as UTC."""
    assert_that(published_in_window(published_at, since)).is_equal_to(inside)
