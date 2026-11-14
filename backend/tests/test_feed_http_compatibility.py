"""Offline regressions for feed HTTP and parser compatibility."""

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import Mock

import feedparser
import httpx
import pytest
from assertpy import assert_that

from podex.config import FeedHttpSettings
from podex.models import Podcast
from podex.services.discovery import DiscoveredPodcast
from podex.services.discovery_rss import RSSDiscovery
from podex.services.feed_http import FeedFetchError, FeedHttpClient

_ADDRESSES = ["2606:4700:4700::1111", "93.184.216.34"]
_URL = "https://feeds.example/start?token=secret"
_FEED = b"""<rss version="2.0"><channel><title>Show \xe9</title>
<image><url>cover.jpg</url></image>
<item><title>Pilot</title><link>episode/1</link></item>
</channel></rss>"""


@pytest.fixture(name="make_client")
def feed_client_factory() -> Mock:
    """Build clients with deterministic dual-stack DNS and fake transports."""

    async def resolve(host: str, port: int) -> list[str]:
        """Return public addresses without network access."""
        del host, port
        return _ADDRESSES

    return Mock(
        side_effect=lambda handler, **kwargs: FeedHttpClient(
            resolver=resolve, transport=httpx.MockTransport(handler), **kwargs
        )
    )


def test_product_headers_reach_every_redirect_hop(make_client: Mock) -> None:
    """Send an anonymous product identity and feed media types on every hop."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Redirect once across hosts, then serve feed bytes."""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302, headers={"location": "https://other.example/rss"}
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    make_client(handler).fetch(_URL)
    assert_that(requests).is_length(2)
    for request in requests:
        assert_that(request.headers.get("user-agent")).is_equal_to(
            "podex-feed-fetcher/1.0"
        )
        assert_that(request.headers.get("accept", "")).contains(
            "application/rss+xml", "application/atom+xml"
        )


@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ConnectTimeout])
def test_connection_failure_tries_next_validated_address(
    make_client: Mock, error: type[httpx.RequestError]
) -> None:
    """Fall back from IPv6 to IPv4 while retaining the TLS/HTTP identity."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Fail establishment on the first address only."""
        requests.append(request)
        if len(requests) == 1:
            raise error("unreachable")
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    make_client(handler).fetch("https://feeds.example:8443/feed")
    assert_that([request.url.host for request in requests]).is_equal_to(_ADDRESSES)
    for request in requests:
        assert_that(request.url.port).is_equal_to(8443)
        assert_that(request.headers["host"]).is_equal_to("feeds.example:8443")
        assert_that(request.extensions["sni_hostname"]).is_equal_to("feeds.example")


@pytest.mark.parametrize("failure", [503, httpx.ReadError, httpx.ReadTimeout])
def test_response_failure_does_not_try_another_address(
    make_client: Mock, failure: int | type[httpx.RequestError]
) -> None:
    """Never replay a request after response work fails."""
    handler = Mock()
    if isinstance(failure, int):
        handler.return_value = httpx.Response(failure)
    else:
        handler.side_effect = failure("response failed with token=secret")
    with pytest.raises(FeedFetchError):
        make_client(handler).fetch(_URL)
    assert_that(handler.call_count).is_equal_to(1)


def test_body_read_error_does_not_retry_and_closes_response(make_client: Mock) -> None:
    """Do not retry a response that fails after streaming its first bytes."""

    class BrokenStream(httpx.AsyncByteStream):
        """Simulate a response disconnect after partial consumption."""

        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            """Deliver a partial body, then fail without network work."""
            yield b"partial"
            raise httpx.ReadError("response failed with token=secret")

        async def aclose(self) -> None:
            """Record cleanup following the read error."""
            self.closed = True

    stream = BrokenStream()
    handler = Mock(return_value=httpx.Response(200, stream=stream))
    with pytest.raises(FeedFetchError) as caught:
        make_client(handler).fetch(_URL)
    assert_that(handler.call_count).is_equal_to(1)
    assert_that(stream.closed).is_true()
    assert_that(str(caught.value)).does_not_contain("secret", "token", "https://")


def test_address_fallback_shares_total_deadline(make_client: Mock) -> None:
    """A second address cannot obtain a fresh total request budget."""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        """Spend most of the deadline connecting, then stall the second address."""
        nonlocal calls
        del request
        calls += 1
        await asyncio.sleep(0.03)
        if calls == 1:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    with pytest.raises(FeedFetchError, match="total deadline"):
        make_client(
            handler, settings=FeedHttpSettings(total_timeout_seconds=0.05)
        ).fetch(_URL)
    assert_that(calls).is_equal_to(2)


def test_relative_links_and_images_use_final_redirect_url(make_client: Mock) -> None:
    """Both discovery paths resolve relative content against the final feed URL."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Redirect across hosts before serving Latin-1 feed bytes."""
        if request.url.path == "/start":
            return httpx.Response(
                302, headers={"location": "https://other.example/shows/feed.xml"}
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/rss+xml; charset=iso-8859-1"},
            stream=httpx.ByteStream(_FEED),
        )

    discovery = RSSDiscovery(make_client(handler))
    podcast = discovery.discover_podcast(_URL)
    assert_that(podcast).is_not_none()
    assert_that(cast(DiscoveredPodcast, podcast).cover_url).is_equal_to(
        "https://other.example/shows/cover.jpg"
    )
    episodes = discovery.discover_episodes(
        Podcast(name="Cafe", slug="cafe", rss_url=_URL)
    )
    assert_that(episodes[0].episode_url).is_equal_to(
        "https://other.example/shows/episode/1"
    )


def test_http_charset_reaches_feedparser_without_bozo(
    make_client: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Honor the HTTP charset for XML without an encoding declaration."""
    real_parse = feedparser.parse
    parser = Mock(wraps=real_parse)
    monkeypatch.setattr("podex.services.discovery_rss.feedparser.parse", parser)
    discovery = RSSDiscovery(
        make_client(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/rss+xml; charset=iso-8859-1"},
                stream=httpx.ByteStream(_FEED),
            )
        )
    )
    discovery.discover_podcast(_URL)
    discovery.discover_episodes(Podcast(name="Cafe", slug="cafe", rss_url=_URL))
    assert_that(parser.call_count).is_equal_to(2)
    for call in parser.call_args_list:
        call.args[0].seek(0)
        parsed = real_parse(*call.args, **call.kwargs)
        assert_that(parsed.bozo).is_false()
        assert_that(parsed.feed.title).is_equal_to("Show \u00e9")


@pytest.mark.parametrize(
    "target", ["http://other.example/feed?token=secret", "http://feeds.example/feed"]
)
def test_https_redirect_downgrade_is_refused(make_client: Mock, target: str) -> None:
    """Reject HTTPS downgrades before any request to the HTTP destination."""
    handler = Mock(return_value=httpx.Response(302, headers={"location": target}))
    with pytest.raises(FeedFetchError) as caught:
        make_client(handler).fetch(_URL)
    assert_that(str(caught.value)).is_equal_to(
        "Feed redirect cannot downgrade HTTPS to HTTP"
    )
    assert_that(handler.call_count).is_equal_to(1)


@pytest.mark.parametrize(
    ("source_scheme", "target_scheme"),
    [("http", "https"), ("http", "http"), ("https", "https")],
)
def test_allowed_redirect_schemes_preserve_nondefault_ports(
    make_client: Mock, source_scheme: str, target_scheme: str
) -> None:
    """Allow upgrades and same-scheme redirects on arbitrary public ports."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Redirect across hosts to a nondefault port, then return bytes."""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"location": f"{target_scheme}://other.example:8088/feed"},
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    result = make_client(handler).fetch(f"{source_scheme}://feeds.example/start")
    assert_that(result.body).is_equal_to(b"feed")
    assert_that(result.final_url).is_equal_to(
        f"{target_scheme}://other.example:8088/feed"
    )
    assert_that(requests).is_length(2)
    assert_that(requests[1].url.port).is_equal_to(8088)
    assert_that(requests[1].headers["host"]).is_equal_to("other.example:8088")


@pytest.mark.parametrize("scheme", ["file", "gopher"])
def test_unsupported_scheme_keeps_specific_safe_error(
    make_client: Mock, scheme: str
) -> None:
    """Keep policy errors specific while excluding input URL details."""
    handler = Mock()
    with pytest.raises(FeedFetchError) as caught:
        make_client(handler).fetch(f"{scheme}://host.example/feed?token=secret")
    assert_that(str(caught.value)).is_equal_to("Feed URL must use HTTP(S) with a host")
    assert_that(handler.call_count).is_zero()


def test_parse_failure_logs_slug_without_url(
    make_client: Mock, caplog: pytest.LogCaptureFixture
) -> None:
    """Identify parse failures by slug without leaking feed credentials."""
    discovery = RSSDiscovery(
        make_client(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"bad"))
        )
    )
    discovery.discover_podcast(_URL)
    discovery.discover_episodes(Podcast(name="Example", slug="example", rss_url=_URL))
    assert_that(caplog.messages).contains(
        "Failed to parse RSS feed for podcast unknown-podcast",
        "Failed to parse RSS feed for podcast example",
    )
    assert_that(caplog.text).does_not_contain("https://", "token", "secret")
