"""Tests for Spotify discovery: client setup, paging, parsing and failures.

The Spotify SDK client is replaced by an in-memory double that serves canned
pages; no request leaves the process.
"""

import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from secrets import token_hex
from typing import Any

import pytest
from assertpy import assert_that

from podex.models import Podcast
from podex.services import discovery_spotify
from podex.services.discovery_spotify import SpotifyDiscovery

_SHOW_ID = "0abc123"
_LOGGER = discovery_spotify.__name__
_CLIENT_ID = f"synthetic-client-{token_hex(4)}"
_CLIENT_SECRET = f"synthetic-secret-{token_hex(8)}"


def _episode(number: int, release_date: str = "2026-01-03") -> dict[str, Any]:
    """Return one Spotify episode item."""
    return {
        "name": f"Episode {number}",
        "release_date": release_date,
        "duration_ms": 60_000,
        "uri": f"spotify:episode:{number}",
        "external_urls": {"spotify": f"https://open.spotify.com/episode/{number}"},
    }


class _PagedSpotify:
    """Spotify client double serving ``show_episodes`` pages and search results."""

    def __init__(
        self,
        pages: list[dict[str, Any] | Exception] | None = None,
        *,
        shows: dict[str, dict[str, Any] | None] | None = None,
        search_result: dict[str, Any] | Exception | None = None,
    ) -> None:
        self.pages = pages or []
        self.shows = shows or {}
        self.search_result = search_result
        self.episode_calls: list[dict[str, Any]] = []
        self.show_calls: list[str] = []
        self.search_calls: list[dict[str, Any]] = []

    def show_episodes(
        self,
        spotify_id: str,
        limit: int,
        offset: int,
        market: str,
    ) -> dict[str, Any]:
        """Return the next page, or raise the scripted error."""
        self.episode_calls.append(
            {"id": spotify_id, "limit": limit, "offset": offset, "market": market},
        )
        page = self.pages[len(self.episode_calls) - 1]
        if isinstance(page, Exception):
            raise page
        return page

    def show(self, spotify_id: str, market: str) -> dict[str, Any] | None:
        """Return the scripted show payload."""
        del market
        self.show_calls.append(spotify_id)
        return self.shows.get(spotify_id)

    def search(self, **params: Any) -> dict[str, Any]:
        """Record the query, then return the scripted payload or raise."""
        self.search_calls.append(params)
        if isinstance(self.search_result, Exception):
            raise self.search_result
        return self.search_result or {}


@dataclass
class _AlwaysFullSpotify:
    """Spotify client double whose show always reports another full page."""

    offsets: list[int] = field(default_factory=list)

    def show_episodes(
        self,
        spotify_id: str,
        limit: int,
        offset: int,
        market: str,
    ) -> dict[str, Any]:
        """Return ``limit`` episodes and a next link, forever."""
        del spotify_id, market
        self.offsets.append(offset)
        return {
            "items": [_episode(offset + index) for index in range(limit)],
            "next": "https://api.spotify.com/v1/shows/x/episodes?offset=next",
        }


@dataclass
class _Sdk:
    """Stand-in for ``spotipy.Spotify`` construction that returns a double."""

    monkeypatch: pytest.MonkeyPatch
    auth_managers: list[Any] = field(default_factory=list)

    def install(self, client: object) -> SpotifyDiscovery:
        """Make the provider's SDK client ``client``; return configured discovery."""

        def build(*, auth_manager: Any) -> object:
            self.auth_managers.append(auth_manager)
            return client

        self.monkeypatch.setattr("spotipy.Spotify", build)
        return SpotifyDiscovery(client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET)


@pytest.fixture(name="sdk")
def sdk_fixture(monkeypatch: pytest.MonkeyPatch) -> _Sdk:
    """Return the SDK construction stand-in for this test."""
    return _Sdk(monkeypatch)


def _podcast(spotify_id: str | None = _SHOW_ID) -> Podcast:
    """Return an unsaved podcast with the given Spotify show id."""
    return Podcast(name="Example Show", slug="example-show", spotify_id=spotify_id)


def _page(items: list[dict[str, Any]], *, more: bool) -> dict[str, Any]:
    """Return a Spotify episodes page."""
    return {"items": items, "next": "https://api.spotify.com/next" if more else None}


def _failure_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the provider's warning-or-worse lines, each checked to be a WARNING."""
    records = [
        record
        for record in caplog.records
        if record.name == _LOGGER and record.levelno >= logging.WARNING
    ]
    assert_that([record.levelname for record in records]).is_equal_to(
        ["WARNING"] * len(records),
    )
    return [record.getMessage() for record in records]


def test_episodes_follow_next_links_with_growing_offsets(sdk: _Sdk) -> None:
    """Pages are requested at the running offset until no next link remains."""
    client = _PagedSpotify(
        [
            _page([_episode(1), _episode(2)], more=True),
            _page([_episode(3), _episode(4)], more=True),
            _page([_episode(5)], more=False),
        ],
    )

    episodes = sdk.install(client).discover_episodes(_podcast(), limit=2)

    assert_that([episode.title for episode in episodes]).is_equal_to(
        ["Episode 1", "Episode 2", "Episode 3", "Episode 4", "Episode 5"],
    )
    assert_that([call["offset"] for call in client.episode_calls]).is_equal_to(
        [0, 2, 4],
    )
    assert_that({call["limit"] for call in client.episode_calls}).is_equal_to({2})
    assert_that({call["id"] for call in client.episode_calls}).is_equal_to(
        {_SHOW_ID},
    )
    assert_that({call["market"] for call in client.episode_calls}).is_equal_to(
        {"US"},
    )


def test_short_page_with_a_next_link_advances_by_the_items_received(
    sdk: _Sdk,
) -> None:
    """The next offset counts the items actually returned, not the page size."""
    client = _PagedSpotify(
        [
            _page([_episode(1), _episode(2)], more=True),
            _page([_episode(3)], more=False),
        ],
    )

    episodes = sdk.install(client).discover_episodes(_podcast(), limit=50)

    assert_that([call["offset"] for call in client.episode_calls]).is_equal_to(
        [0, 2],
    )
    assert_that(episodes).is_length(3)


def test_episode_page_size_is_capped_at_the_api_maximum(sdk: _Sdk) -> None:
    """A requested page size above 50 is sent as 50."""
    client = _PagedSpotify([_page([_episode(1)], more=False)])

    sdk.install(client).discover_episodes(_podcast(), limit=500)

    assert_that(client.episode_calls[0]["limit"]).is_equal_to(50)


def test_empty_page_ends_paging_even_with_a_next_link(sdk: _Sdk) -> None:
    """An empty item list stops the walk although the API offers another page."""
    client = _PagedSpotify(
        [_page([_episode(1)], more=True), _page([], more=True), _page([], more=False)],
    )

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that(episodes).is_length(1)
    assert_that(client.episode_calls).is_length(2)


def test_paging_stops_at_the_thousand_episode_safety_limit(sdk: _Sdk) -> None:
    """A show that always reports more pages is read up to 1,000 episodes."""
    client = _AlwaysFullSpotify()

    episodes = sdk.install(client).discover_episodes(_podcast(), limit=50)

    assert_that(episodes).is_length(1000)
    assert_that(client.offsets).is_equal_to(list(range(0, 1000, 50)))


def test_since_stops_at_the_first_older_episode(sdk: _Sdk) -> None:
    """Newest-first paging returns only newer episodes and fetches no more pages."""
    client = _PagedSpotify(
        [
            _page(
                [
                    _episode(3, "2026-03-01"),
                    _episode(2, "2026-02-01"),
                    _episode(1, "2026-01-01"),
                ],
                more=True,
            ),
            _page([_episode(0, "2025-12-01")], more=False),
        ],
    )

    episodes = sdk.install(client).discover_episodes(
        _podcast(),
        since=datetime(2026, 1, 15, tzinfo=UTC),
    )

    assert_that([episode.title for episode in episodes]).is_equal_to(
        ["Episode 3", "Episode 2"],
    )
    assert_that(client.episode_calls).is_length(1)


def test_since_keeps_undated_episodes(sdk: _Sdk) -> None:
    """Episodes without a release date are never treated as older than ``since``."""
    undated = _episode(1)
    del undated["release_date"]
    client = _PagedSpotify([_page([undated], more=False)])

    episodes = sdk.install(client).discover_episodes(
        _podcast(),
        since=datetime(2026, 1, 15, tzinfo=UTC),
    )

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].published_at).is_none()


def test_failure_on_a_later_page_keeps_earlier_episodes(
    caplog: pytest.LogCaptureFixture,
    sdk: _Sdk,
) -> None:
    """A page that fails ends paging, keeps what was read and logs the failure."""
    client = _PagedSpotify(
        [_page([_episode(1)], more=True), RuntimeError("provider unavailable")],
    )
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that(episodes).is_length(1)
    assert_that(_failure_lines(caplog)).is_equal_to(
        [
            f"('episode_fetch', '{_SHOW_ID}') spotify: RuntimeError; request_failed",
        ],
    )


def test_items_without_a_name_are_skipped(sdk: _Sdk) -> None:
    """Unnamed items are dropped while the rest of the page is kept."""
    nameless = _episode(2)
    nameless["name"] = ""
    client = _PagedSpotify([_page([_episode(1), nameless, _episode(3)], more=False)])

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that([episode.title for episode in episodes]).is_equal_to(
        ["Episode 1", "Episode 3"],
    )


def test_podcast_without_spotify_id_is_not_queried(
    caplog: pytest.LogCaptureFixture,
    sdk: _Sdk,
) -> None:
    """A podcast with no Spotify show id returns nothing and makes no request."""
    client = _PagedSpotify([])
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    episodes = sdk.install(client).discover_episodes(_podcast(spotify_id=None))

    assert_that(episodes).is_empty()
    assert_that(client.episode_calls).is_empty()
    assert_that(_failure_lines(caplog)).is_equal_to(
        ["Podcast example-show has no spotify_id"],
    )


@pytest.mark.parametrize(
    ("release_date", "expected"),
    [
        ("2026-04-09", datetime(2026, 4, 9, tzinfo=UTC)),
        ("2026-04", datetime(2026, 4, 1, tzinfo=UTC)),
        ("2026", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-13-40", None),
        ("2026-1", None),
        ("", None),
    ],
    ids=["day", "month", "year", "invalid-day", "unknown-shape", "empty"],
)
def test_release_date_precision_is_parsed_or_ignored(
    release_date: str,
    expected: datetime | None,
    sdk: _Sdk,
) -> None:
    """Day, month and year precision parse to UTC; anything else is left unset."""
    client = _PagedSpotify([_page([_episode(1, release_date)], more=False)])

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].published_at).is_equal_to(expected)


@pytest.mark.parametrize(
    ("length", "expected_length", "suffixed"),
    [(1000, 1000, False), (1001, 1003, True)],
    ids=["at-limit", "over-limit"],
)
def test_description_is_cut_after_a_thousand_characters(
    sdk: _Sdk,
    length: int,
    expected_length: int,
    suffixed: bool,
) -> None:
    """Descriptions up to 1,000 characters are kept; longer ones are cut and marked."""
    item = _episode(1)
    item["description"] = "".join(str(index % 10) for index in range(length))
    client = _PagedSpotify([_page([item], more=False)])

    episodes = sdk.install(client).discover_episodes(_podcast())

    description = episodes[0].description or ""
    assert_that(len(description)).is_equal_to(expected_length)
    assert_that(description[:1000]).is_equal_to(item["description"][:1000])
    assert_that(description.endswith("...")).is_equal_to(suffixed)


@pytest.mark.parametrize(
    ("client_id", "client_secret"),
    [(_CLIENT_ID, None), (None, _CLIENT_SECRET), (None, None)],
    ids=["id-only", "secret-only", "neither"],
)
def test_partial_credentials_are_not_configured(
    monkeypatch: pytest.MonkeyPatch,
    client_id: str | None,
    client_secret: str | None,
) -> None:
    """Both the client id and the secret are needed; one alone is not enough."""
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)

    spotify = SpotifyDiscovery(client_id=client_id, client_secret=client_secret)

    assert_that(spotify.is_configured).is_false()
    assert_that(spotify.discover_podcast(_SHOW_ID)).is_none()


def test_optional_episode_fields_default_to_none(sdk: _Sdk) -> None:
    """Missing duration, links, images and description stay unset."""
    client = _PagedSpotify([_page([{"name": "Bare episode"}], more=False)])

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that(episodes).is_length(1)
    episode = episodes[0]
    assert_that(episode.title).is_equal_to("Bare episode")
    assert_that(episode.duration_seconds).is_none()
    assert_that(episode.episode_url).is_none()
    assert_that(episode.thumbnail_url).is_none()
    assert_that(episode.spotify_uri).is_none()
    assert_that(episode.description).is_none()
    assert_that(episode.has_transcript).is_false()
    assert_that(episode.discovery_source).is_equal_to("spotify")


def test_episode_thumbnail_is_the_tallest_image(sdk: _Sdk) -> None:
    """The tallest image wins; images without a height sort last."""
    item = _episode(1)
    item["description"] = "Short description."
    item["images"] = [
        {"url": "https://i.scdn.co/unsized"},
        {"url": "https://i.scdn.co/small", "height": 64},
        {"url": "https://i.scdn.co/large", "height": 640},
    ]
    client = _PagedSpotify([_page([item], more=False)])

    episodes = sdk.install(client).discover_episodes(_podcast())

    assert_that(episodes[0].thumbnail_url).is_equal_to("https://i.scdn.co/large")
    assert_that(episodes[0].description).is_equal_to("Short description.")
    assert_that(episodes[0].duration_seconds).is_equal_to(60)


def test_missing_show_returns_none_without_logging(
    caplog: pytest.LogCaptureFixture,
    sdk: _Sdk,
) -> None:
    """An empty show payload is reported as not found rather than as a failure."""
    client = _PagedSpotify(shows={_SHOW_ID: None})
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    podcast = sdk.install(client).discover_podcast(_SHOW_ID)

    assert_that(podcast).is_none()
    assert_that(client.show_calls).is_equal_to([_SHOW_ID])
    assert_that(_failure_lines(caplog)).is_empty()


def test_show_defaults_when_metadata_is_missing(sdk: _Sdk) -> None:
    """A show with no name or images still maps to a podcast with defaults."""
    client = _PagedSpotify(shows={_SHOW_ID: {"id": _SHOW_ID}})

    podcast = sdk.install(client).discover_podcast(_SHOW_ID)

    assert_that(podcast).is_not_none()
    if podcast is None:
        raise AssertionError("expected a podcast")
    assert_that(podcast.name).is_equal_to("Unknown Podcast")
    assert_that(podcast.slug).is_equal_to("unknown-podcast")
    assert_that(podcast.cover_url).is_none()
    assert_that(podcast.spotify_id).is_equal_to(_SHOW_ID)
    assert_that(podcast.discovery_source).is_equal_to("spotify")


def test_search_discovers_each_identified_show(sdk: _Sdk) -> None:
    """Search results without an id or without show metadata are dropped."""
    client = _PagedSpotify(
        shows={
            "show-one": {"name": "First Show"},
            "show-gone": None,
            "show-two": {"name": "Second Show"},
        },
        search_result={
            "shows": {
                "items": [
                    {"id": "show-one"},
                    {"name": "No identifier"},
                    {"id": "show-gone"},
                    {"id": "show-two"},
                ],
            },
        },
    )

    podcasts = sdk.install(client).search_podcasts("example")

    assert_that([podcast.name for podcast in podcasts]).is_equal_to(
        ["First Show", "Second Show"],
    )
    assert_that(client.search_calls).is_equal_to(
        [{"q": "example", "type": "show", "limit": 10, "market": "US"}],
    )
    assert_that(client.show_calls).is_equal_to(["show-one", "show-gone", "show-two"])


def test_search_failure_returns_no_results(
    caplog: pytest.LogCaptureFixture,
    sdk: _Sdk,
) -> None:
    """A failing search is logged with its operation and returns an empty list."""
    client = _PagedSpotify(search_result=RuntimeError("search unavailable"))
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    podcasts = sdk.install(client).search_podcasts("example")

    assert_that(podcasts).is_empty()
    assert_that(client.show_calls).is_empty()
    assert_that(_failure_lines(caplog)).is_equal_to(
        ["('search',) spotify: RuntimeError; request_failed"],
    )


def test_missing_sdk_logs_import_error_and_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without the SDK installed every operation degrades and logs ImportError."""
    monkeypatch.setitem(sys.modules, "spotipy", None)
    monkeypatch.setitem(sys.modules, "spotipy.oauth2", None)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    spotify = SpotifyDiscovery(client_id=_CLIENT_ID, client_secret=_CLIENT_SECRET)

    assert_that(spotify.is_configured).is_true()
    assert_that(spotify.discover_episodes(_podcast())).is_empty()
    assert_that(spotify.discover_podcast(_SHOW_ID)).is_none()
    assert_that(spotify.search_podcasts("example")).is_empty()
    assert_that(_failure_lines(caplog)).is_equal_to(
        [
            f"('configuration', '{_SHOW_ID}') spotify: ImportError; request_failed",
            f"('show_fetch', '{_SHOW_ID}') spotify: ImportError; request_failed",
            "('search',) spotify: ImportError; request_failed",
        ],
    )
    error = caplog.records[0].exc_info
    assert_that(error).is_not_none()
    # The log boundary attaches a same-named stand-in, not the original error.
    assert_that(error[0].__name__ if error and error[0] else "").is_equal_to(
        "ImportError",
    )


def test_client_is_built_once_from_the_configured_credentials(sdk: _Sdk) -> None:
    """The SDK client is created on first use and reused by later calls."""
    client = _PagedSpotify(
        [_page([_episode(1)], more=False)],
        shows={_SHOW_ID: {"name": "Example Show"}},
    )
    spotify = sdk.install(client)

    podcast = spotify.discover_podcast(_SHOW_ID)
    episodes = spotify.discover_episodes(_podcast())

    assert_that(podcast).is_not_none()
    assert_that(episodes).is_length(1)
    assert_that(sdk.auth_managers).is_length(1)
    manager = sdk.auth_managers[0]
    assert_that(manager.client_id).is_equal_to(_CLIENT_ID)
    assert_that(manager.client_secret).is_equal_to(_CLIENT_SECRET)
