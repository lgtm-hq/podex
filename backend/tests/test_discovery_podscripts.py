"""Tests for Podscripts discovery: page parsing, listing and fetch failures.

Pages are served by ``httpx.MockTransport``; ``time.sleep`` is recorded
instead of waited on, so the politeness delay is asserted, not spent.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx
import pytest
from assertpy import assert_that

from podex.models import Podcast
from podex.services import discovery_podscripts
from podex.services.discovery_podscripts import PodscriptsDiscovery

_LOGGER = discovery_podscripts.__name__
_DELAY = 0.25

Route = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(name="sleeps")
def sleeps_fixture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every requested sleep instead of waiting."""
    recorded: list[float] = []
    monkeypatch.setattr(discovery_podscripts.time, "sleep", recorded.append)
    return recorded


@pytest.fixture(name="serve")
def serve_fixture(
    sleeps: list[float],
) -> Iterator[Callable[[Route], PodscriptsDiscovery]]:
    """Return a factory for discovery whose client is served by ``route``."""
    del sleeps
    opened: list[PodscriptsDiscovery] = []

    def build(route: Route) -> PodscriptsDiscovery:
        source = PodscriptsDiscovery(delay=_DELAY)
        source.client.close()
        source.client = httpx.Client(transport=httpx.MockTransport(route))
        opened.append(source)
        return source

    yield build
    for source in opened:
        source.close()


def _html(body: str, status: int = 200) -> Route:
    """Serve the same body and status for every request."""
    return lambda _request: httpx.Response(status, text=body)


def _pages(pages: dict[int, str], failing: dict[int, int] | None = None) -> Route:
    """Serve listing pages by number; ``failing`` maps a page to an error status."""
    errors = failing or {}

    def route(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        if page in errors:
            return httpx.Response(errors[page])
        return httpx.Response(200, text=pages.get(page, ""))

    return route


def _podcast() -> Podcast:
    """Return an unsaved podcast that has a Podscripts slug."""
    return Podcast(name="Example Show", slug="example-show", podscripts_slug="show")


def _records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """Return the provider's messages at exactly ``level``."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == _LOGGER and r.levelno == level
    ]


def test_provider_identity_needs_no_configuration(
    serve: Callable[[Route], PodscriptsDiscovery],
) -> None:
    """Podscripts needs no credentials and names itself ``podscripts``."""
    source = serve(_html(""))

    assert_that(source.name).is_equal_to("podscripts")
    assert_that(source.is_configured).is_true()


@pytest.mark.parametrize(
    ("head", "body", "expected"),
    [
        ("<title>Example Show - Podscripts</title>", "", "Example Show"),
        ("<title>Example Show | Transcripts</title>", "", "Example Show"),
        (
            "<title>Title Name - Podscripts</title>",
            "<h1>Heading Name</h1>",
            "Heading Name",
        ),
        ("", "", "Example Show Slug"),
        ("<title></title>", "<h1></h1>", "Example Show Slug"),
    ],
    ids=["title-dash", "title-pipe", "h1-wins", "slug-fallback", "empty-tags"],
)
def test_podcast_name_comes_from_heading_title_or_slug(
    serve: Callable[[Route], PodscriptsDiscovery],
    sleeps: list[float],
    head: str,
    body: str,
    expected: str,
) -> None:
    """The h1 wins over the title, and the slug is the last resort."""
    source = serve(_html(f"<html><head>{head}</head><body>{body}</body></html>"))

    podcast = source.discover_podcast("example-show-slug")

    assert_that(podcast).is_not_none()
    if podcast is None:
        raise AssertionError("expected a podcast")
    assert_that(podcast.name).is_equal_to(expected)
    assert_that(podcast.slug).is_equal_to("example-show-slug")
    assert_that(podcast.podscripts_slug).is_equal_to("example-show-slug")
    assert_that(podcast.discovery_source).is_equal_to("podscripts")
    assert_that(podcast.description).is_none()
    assert_that(podcast.cover_url).is_none()
    assert_that(sleeps).is_equal_to([_DELAY])


def test_podcast_page_fetch_failure_returns_none(
    serve: Callable[[Route], PodscriptsDiscovery],
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing podcast page is logged with its slug and yields no podcast."""
    requests: list[httpx.Request] = []

    def missing(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    caplog.set_level(logging.WARNING, logger=_LOGGER)

    podcast = serve(missing).discover_podcast("example-show")

    assert_that(podcast).is_none()
    assert_that(requests).is_length(1)
    assert_that(sleeps).is_empty()
    assert_that(_records(caplog, logging.WARNING)).is_equal_to(
        [
            "('podcast_fetch', 'example-show') "
            "podscripts: HTTPStatusError (HTTP 404); request_failed",
        ],
    )


def test_failed_later_page_keeps_earlier_episodes(
    serve: Callable[[Route], PodscriptsDiscovery],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A page that cannot be fetched ends the walk with the pages already read."""
    page1 = '<a href="/podcasts/show/1-first-episode">First</a>'
    caplog.set_level(logging.INFO, logger=_LOGGER)

    episodes = serve(_pages({1: page1}, failing={2: 404})).discover_episodes(
        _podcast(),
    )

    assert_that([episode.title for episode in episodes]).is_equal_to(["First"])
    assert_that(_records(caplog, logging.WARNING)).is_equal_to(
        [
            "('episode_page', 'show', 2) "
            "podscripts: HTTPStatusError (HTTP 404); request_failed",
        ],
    )
    assert_that(_records(caplog, logging.INFO)).is_equal_to(
        ["Discovered 1 episodes from podscripts.co"],
    )


def test_since_does_not_filter_listing_pages(
    serve: Callable[[Route], PodscriptsDiscovery],
) -> None:
    """Listing pages carry no dates, so every episode is returned to the caller."""
    page1 = (
        '<a href="/podcasts/show/2-second">Second</a>'
        '<a href="/podcasts/show/1-first">First</a>'
    )

    episodes = serve(_pages({1: page1})).discover_episodes(
        _podcast(),
        since=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert_that([episode.episode_number for episode in episodes]).is_equal_to([2, 1])


def test_paging_stops_at_the_two_hundred_page_safety_limit(
    serve: Callable[[Route], PodscriptsDiscovery],
    sleeps: list[float],
) -> None:
    """A listing that never runs out of pages is read for 200 pages."""
    requested: list[int] = []

    def endless(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested.append(page)
        return httpx.Response(200, text=f'<a href="/podcasts/show/{page}-ep">Ep</a>')

    episodes = serve(endless).discover_episodes(_podcast())

    assert_that(requested).is_equal_to(list(range(1, 201)))
    assert_that(episodes).is_length(200)
    assert_that(sleeps).is_equal_to([_DELAY] * 200)


@pytest.mark.parametrize(
    ("href", "text", "number", "title"),
    [
        (
            "/podcasts/show/42-the-answer",
            "The Answer, Explained",
            42,
            "The Answer, Explained",
        ),
        ("/podcasts/show/42-the-answer", "", 42, "The Answer"),
        ("/podcasts/show/42-the-answer", "3 comments", 42, "The Answer"),
        ("/podcasts/show/42-the-answer/", "1 Comment", 42, "The Answer"),
        ("/podcasts/show/bonus-special", "Bonus: Special", None, "Bonus: Special"),
        ("/podcasts/show/bonus-special", "12 comments", None, "Bonus Special"),
    ],
    ids=[
        "numbered-text",
        "numbered-empty-text",
        "numbered-comment-label",
        "trailing-slash",
        "unnumbered-text",
        "unnumbered-comment-label",
    ],
)
def test_episode_title_and_number_come_from_link_or_slug(
    serve: Callable[[Route], PodscriptsDiscovery],
    href: str,
    text: str,
    number: int | None,
    title: str,
) -> None:
    """Link text names the episode unless it is empty or only a comment count."""
    episodes = serve(_pages({1: f'<a href="{href}">{text}</a>'})).discover_episodes(
        _podcast(),
    )

    assert_that(episodes).is_length(1)
    assert_that(episodes[0].episode_number).is_equal_to(number)
    assert_that(episodes[0].title).is_equal_to(title)
    assert_that(episodes[0].episode_url).is_equal_to(f"https://podscripts.co{href}")
    assert_that(episodes[0].has_transcript).is_true()


def test_listing_ignores_links_outside_the_show(
    serve: Callable[[Route], PodscriptsDiscovery],
) -> None:
    """Only links below the show's own path are episodes."""
    page1 = (
        '<a href="/podcasts/show">Show index</a>'
        '<a href="/podcasts/show/">Show index slash</a>'
        '<a href="/podcasts/other-show/1-elsewhere">Other show</a>'
        '<a href="https://podscripts.co/podcasts/show/2-absolute">Absolute</a>'
        '<a href="/podcasts/show/3-kept">Kept</a>'
        "<a>No href</a>"
    )

    episodes = serve(_pages({1: page1})).discover_episodes(_podcast())

    assert_that([episode.title for episode in episodes]).is_equal_to(["Kept"])


def test_catalog_lists_unique_top_level_podcasts(
    serve: Callable[[Route], PodscriptsDiscovery],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The catalog keeps one entry per top-level podcast link."""
    catalog = (
        '<a href="/podcasts/alpha-show">Alpha Show</a>'
        '<a href="/podcasts/alpha-show/">Alpha again</a>'
        '<a href="/podcasts/beta-show/"></a>'
        '<a href="/podcasts/alpha-show/7-episode">Episode link</a>'
        '<a href="/podcasts">All podcasts</a>'
        '<a href="/podcasts/">All podcasts slash</a>'
        '<a href="/about">About</a>'
    )
    caplog.set_level(logging.INFO, logger=_LOGGER)

    listed = serve(_html(catalog)).list_available_podcasts()

    assert_that(listed).is_equal_to(
        [
            {
                "slug": "alpha-show",
                "name": "Alpha Show",
                "url": "https://podscripts.co/podcasts/alpha-show",
            },
            {
                "slug": "beta-show",
                "name": "Beta Show",
                "url": "https://podscripts.co/podcasts/beta-show/",
            },
        ],
    )
    assert_that(_records(caplog, logging.INFO)).is_equal_to(
        ["Found 2 podcasts on podscripts.co"],
    )


def test_catalog_without_podcast_links_is_empty(
    serve: Callable[[Route], PodscriptsDiscovery],
) -> None:
    """A catalog page with no podcast links lists nothing."""
    listed = serve(
        _html("<html><body><p>Maintenance</p></body></html>")
    ).list_available_podcasts()

    assert_that(listed).is_empty()


def test_catalog_fetch_failure_is_logged_as_an_error(
    serve: Callable[[Route], PodscriptsDiscovery],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unreachable catalog lists nothing and logs at error level."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    caplog.set_level(logging.WARNING, logger=_LOGGER)

    listed = serve(refuse).list_available_podcasts()

    assert_that(listed).is_empty()
    assert_that(_records(caplog, logging.ERROR)).is_equal_to(
        ["('catalog_fetch',) podscripts: ConnectError; request_failed"],
    )


def test_context_manager_closes_the_client(
    serve: Callable[[Route], PodscriptsDiscovery],
) -> None:
    """Leaving the ``with`` block closes the HTTP client."""
    source = serve(_html(""))

    with source as entered:
        assert_that(entered).is_same_as(source)
        assert_that(source.client.is_closed).is_false()

    assert_that(source.client.is_closed).is_true()
