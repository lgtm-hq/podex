"""Regression tests for the outbound RSS request boundary."""

import asyncio
import gzip
import ipaddress
import ssl
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import anyio
import anyio.streams.tls
import httpx
import pytest
from assertpy import assert_that
from pydantic import ValidationError

from podex.config import FeedHttpSettings, Settings
from podex.services.discovery_rss import RSSDiscovery
from podex.services.feed_http import (
    FeedFetchError,
    FeedHttpClient,
    Resolver,
    public_address,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/feed",
        "http://[::1]/feed",
        "http://169.254.169.254/latest/meta-data",
        "http://[fd00:ec2::254]/feed",
    ],
)
def test_rss_rejects_private_feed_before_fetch(
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private feed URLs must fail before feedparser can perform HTTP work."""
    parser = Mock(return_value=Mock(bozo=True, entries=[]))
    monkeypatch.setattr("podex.services.discovery_rss.feedparser.parse", parser)

    with pytest.raises(ValueError, match="public"):
        RSSDiscovery().discover_podcast(url)

    assert_that(parser.call_count).is_zero()


async def _public_dns(host: str, port: int) -> list[str]:
    """Resolve test hosts without performing network work."""
    del host, port
    return ["93.184.216.34"]


class _Chunks(httpx.AsyncByteStream):
    """Record streaming and cleanup without sockets."""

    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False
        self.consumed = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        """Yield deterministic chunks, optionally stalling for deadline tests."""
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        """Record response cleanup on success and failure."""
        self.closed = True


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    settings: FeedHttpSettings | None = None,
    resolver: Resolver = _public_dns,
) -> FeedHttpClient:
    """Build the real boundary with fake DNS and transport."""
    return FeedHttpClient(
        settings,
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        str(ipaddress.IPv4Address(0)),
        "192.0.2.1",
        "224.0.0.1",
        "240.0.0.1",
        "::",
        "fe80::1",
        "fec0::1",
        "2001:db8::1",
        "ff02::1",
        "::ffff:8.8.8.8",
        "2002:0808:0808::1",
        "64:ff9b::808:808",
        "64:ff9b:1::a00:1",
        "192.0.0.8",
        "192.88.99.1",
        "3fff::1",
        "2001::1",
        "fe80::1%eth0",
    ],
)
def test_nonpublic_and_transition_destinations_are_rejected(address: str) -> None:
    """Reject private, reserved, multicast, scoped and IP transition targets."""
    with pytest.raises(FeedFetchError, match="public"):
        public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://feeds.example/a",
        "data:text/plain,x",
        "/feed",
        "https://",
        "http://[::1",
        "http://[fe80::1%25eth0]/",
    ],
)
def test_rss_unsupported_scheme_and_invalid_url(url: str) -> None:
    """Reject invalid targets before DNS, transport or parser work."""
    handler = Mock()
    with pytest.raises(FeedFetchError):
        _client(handler).fetch(url)
    assert_that(handler.call_count).is_zero()


def test_rss_redirect_to_private_is_blocked() -> None:
    """A public response cannot redirect the next connection to metadata."""
    handler = Mock(
        return_value=httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data"}
        )
    )
    with pytest.raises(FeedFetchError, match="public"):
        _client(handler).fetch("https://feeds.example/feed")
    assert_that(handler.call_count).is_equal_to(1)


def test_rss_public_to_private_dns_rebinding_is_blocked() -> None:
    """Re-resolve on each redirect and never connect to a changed private answer."""
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])
    requests: list[httpx.Request] = []

    async def resolve(host: str, port: int) -> list[str]:
        """Return a public answer once, then a rebound answer."""
        del host, port
        return next(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the actual pinned destination, then redirect to the same host."""
        requests.append(request)
        return httpx.Response(302, headers={"location": "/other"})

    with pytest.raises(FeedFetchError, match="public"):
        _client(handler, resolver=resolve).fetch("https://feeds.example/feed")
    assert_that(requests).is_length(1)
    assert_that(requests[0].url.host).is_equal_to("93.184.216.34")
    assert_that(requests[0].headers["host"]).is_equal_to("feeds.example")
    assert_that(requests[0].extensions["sni_hostname"]).is_equal_to("feeds.example")


@pytest.mark.parametrize(
    "addresses", [["93.184.216.34", "10.0.0.1"], ["::1", "2606:4700:4700::1111"]]
)
def test_mixed_dns_answers_are_rejected(addresses: list[str]) -> None:
    """Reject the entire target if any DNS answer is non-public."""

    async def resolve(host: str, port: int) -> list[str]:
        """Return mixed DNS answers."""
        del host, port
        return addresses

    handler = Mock()
    with pytest.raises(FeedFetchError, match="public"):
        _client(handler, resolver=resolve).fetch("https://feeds.example/feed")
    assert_that(handler.call_count).is_zero()


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
def test_public_literals_preserve_host_and_tls_identity(address: str) -> None:
    """Public IPv4 and IPv6 literals work without DNS and preserve nondefault ports."""
    host = f"[{address}]" if ":" in address else address
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Record the numeric destination and identity headers."""
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    client = _client(
        handler, resolver=Mock(side_effect=AssertionError("unexpected DNS"))
    )
    assert_that(client.fetch(f"https://{host}:8443/feed")).is_equal_to(b"feed")
    assert_that(requests[0].url.host).is_equal_to(address)
    assert_that(requests[0].url.port).is_equal_to(8443)
    assert_that(requests[0].headers["host"]).is_equal_to(f"{host}:8443")
    assert_that(requests[0].extensions["sni_hostname"]).is_equal_to(address)


def test_relative_public_redirect_and_no_cookie_forwarding() -> None:
    """Resolve relative redirects against the original URL without carrying cookies."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Redirect once, then serve bytes."""
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                307,
                headers={
                    "location": "../feed?key=redacted",
                    "set-cookie": "auth=secret",
                },
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"feed"))

    assert_that(_client(handler).fetch("https://feeds.example/path/start")).is_equal_to(
        b"feed"
    )
    assert_that(requests[1].url.path).is_equal_to("/feed")
    assert_that(requests[1].headers).does_not_contain_key("cookie")


def test_rss_redirect_loop_stops_at_limit() -> None:
    """Loops stop after the configured number of hops without reading bodies."""
    handler = Mock(
        side_effect=lambda request: httpx.Response(301, headers={"location": "/feed"})
    )
    with pytest.raises(FeedFetchError, match="redirect limit"):
        _client(handler, FeedHttpSettings(max_redirects=2)).fetch(
            "https://feeds.example/feed"
        )
    assert_that(handler.call_count).is_equal_to(3)


@pytest.mark.parametrize(
    ("chunks", "encoding", "limits", "message"),
    [
        (
            [b"123", b"456", b"unused"],
            "identity",
            {"max_response_bytes": 5},
            "streaming",
        ),
        (
            [gzip.compress(b"x" * 100)],
            "gzip",
            {"max_response_bytes": 10},
            "compressed/streaming",
        ),
        (
            [gzip.compress(b"x" * 100_000)],
            "gzip",
            {"max_decompressed_bytes": 100},
            "decompressed",
        ),
        ([b"123", b"456"], "identity", {"max_decompressed_bytes": 5}, "decompressed"),
    ],
)
def test_rss_oversized_streaming_and_gzip_body(
    chunks: list[bytes],
    encoding: str,
    limits: dict[str, int],
    message: str,
) -> None:
    """Stop streaming and decompression as soon as either byte cap is exceeded."""
    stream = _Chunks(chunks)
    settings = FeedHttpSettings.model_validate(limits)
    client = _client(
        lambda request: httpx.Response(
            200, headers={"content-encoding": encoding}, stream=stream
        ),
        settings,
    )
    with pytest.raises(FeedFetchError, match=message):
        client.fetch("https://feeds.example/feed")
    assert_that(stream.closed).is_true()
    assert_that(stream.consumed).is_less_than_or_equal_to(2)


@pytest.mark.parametrize("encoding", ["identity", "gzip"])
def test_valid_response_at_size_limit(encoding: str) -> None:
    """Exactly bounded bytes remain valid for identity and gzip responses."""
    body = b"sample feed"
    raw = gzip.compress(body) if encoding == "gzip" else body
    settings = FeedHttpSettings(
        max_response_bytes=len(raw), max_decompressed_bytes=len(body)
    )
    stream = _Chunks([raw[:5], raw[5:]])
    client = _client(
        lambda request: httpx.Response(
            200, headers={"content-encoding": encoding}, stream=stream
        ),
        settings,
    )
    assert_that(client.fetch("https://feeds.example/feed")).is_equal_to(body)
    assert_that(stream.closed).is_true()


@pytest.mark.parametrize(
    "data",
    [
        b"invalid",
        gzip.compress(b"feed")[:-1],
        gzip.compress(b"one") + gzip.compress(b"two"),
    ],
)
def test_invalid_gzip_is_rejected(data: bytes) -> None:
    """Reject corrupt, incomplete or concatenated gzip without parser fallback."""
    client = _client(
        lambda request: httpx.Response(
            200, headers={"content-encoding": "gzip"}, stream=httpx.ByteStream(data)
        )
    )
    with pytest.raises(FeedFetchError, match="gzip"):
        client.fetch("https://feeds.example/feed")


def test_rss_total_deadline_during_streaming() -> None:
    """A slow response cannot hold discovery indefinitely, and its stream closes."""
    stream = _Chunks([b"feed"], delay=1)
    settings = FeedHttpSettings(total_timeout_seconds=0.01)
    client = _client(lambda request: httpx.Response(200, stream=stream), settings)
    with pytest.raises(FeedFetchError, match="total deadline"):
        client.fetch("https://feeds.example/feed")
    assert_that(stream.closed).is_true()


def test_rss_deadline_during_dns() -> None:
    """DNS work is included in the same total request deadline."""

    async def resolve(host: str, port: int) -> list[str]:
        """Stall until the request is cancelled."""
        del host, port
        await asyncio.sleep(1)
        return ["93.184.216.34"]

    handler = Mock()
    with pytest.raises(FeedFetchError, match="total deadline"):
        _client(
            handler, FeedHttpSettings(total_timeout_seconds=0.01), resolver=resolve
        ).fetch("https://feeds.example/feed")
    assert_that(handler.call_count).is_zero()


def test_deadline_covers_headers_and_all_redirects() -> None:
    """The total deadline also cancels a redirected request waiting for headers."""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        """Redirect immediately, then stall the next response headers."""
        nonlocal calls
        del request
        calls += 1
        if calls > 1:
            await asyncio.Event().wait()
        return httpx.Response(302, headers={"location": "/next"})

    client = FeedHttpClient(
        FeedHttpSettings(total_timeout_seconds=0.1),
        resolver=_public_dns,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(FeedFetchError, match="total deadline"):
        client.fetch("https://feeds.example/feed")
    assert_that(calls).is_equal_to(2)


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, OSError]
)
def test_http_errors_are_safe_and_actionable(error: type[Exception]) -> None:
    """Do not expose transport exception URLs, credentials or query strings."""
    credential_url = str(
        httpx.URL("https://feeds.example/path?token=secret").copy_with(
            userinfo=b"test-user:test-pass"
        )
    )
    client = _client(Mock(side_effect=error(credential_url)))
    with pytest.raises(FeedFetchError, match="Feed .* (timeout|failed)") as caught:
        client.fetch("https://feeds.example/feed?token=secret")
    assert_that(str(caught.value)).does_not_contain("secret", "user", "token")


@pytest.mark.parametrize(
    "url",
    [
        str(
            httpx.URL("https://feeds.example/feed").copy_with(
                userinfo=b"test-user:test-pass"
            )
        ),
        "https://user@feeds.example/feed",
    ],
)
def test_url_credentials_are_rejected_without_logging(
    url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Credential-bearing URLs never reach DNS, transport or log output."""
    handler = Mock()
    with pytest.raises(FeedFetchError) as caught:
        _client(handler).fetch(url)
    assert_that(str(caught.value)).does_not_contain("test-pass", "test-user")
    assert_that(caplog.text).does_not_contain("test-pass", "test-user")
    assert_that(handler.call_count).is_zero()


def test_query_credentials_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Successful HTTP work also avoids the default URL-bearing HTTPX INFO log."""
    client = _client(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(b"feed"))
    )
    assert_that(client.fetch("https://feeds.example/feed?token=secret")).is_equal_to(
        b"feed"
    )
    assert_that(caplog.text).does_not_contain("secret")


def test_default_transport_disables_proxy_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real transport configuration explicitly ignores environment proxies."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    factory = Mock(
        return_value=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=httpx.ByteStream(b"feed"))
        )
    )
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    settings = FeedHttpSettings(connect_timeout_seconds=0.5, read_timeout_seconds=0.75)
    client = FeedHttpClient(settings, resolver=_public_dns)
    assert_that(client.fetch("https://feeds.example/feed")).is_equal_to(b"feed")
    factory.assert_called_once_with(trust_env=False, retries=0)


@pytest.mark.parametrize(
    "field",
    ["connect_timeout_seconds", "read_timeout_seconds", "total_timeout_seconds"],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_deadline_settings_require_finite_positive_values(
    field: str, value: float
) -> None:
    """No configured timeout can create an unbounded request."""
    with pytest.raises(ValidationError):
        FeedHttpSettings.model_validate({field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_response_bytes", 0),
        ("max_decompressed_bytes", -1),
        ("max_redirects", -1),
        ("max_redirects", 21),
    ],
)
def test_byte_and_redirect_limits_are_validated(field: str, value: int) -> None:
    """Reject invalid byte caps and excessively long redirect chains."""
    with pytest.raises(ValidationError):
        FeedHttpSettings.model_validate({field: value})


def test_feed_settings_use_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deployment overrides use the ADR 0001 nested settings grammar."""
    monkeypatch.setenv("PODEX_FEED_HTTP__MAX_RESPONSE_BYTES", "1024")
    assert_that(Settings().feed_http.max_response_bytes).is_equal_to(1024)


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (503, {}, "HTTP status 503"),
        (302, {}, "missing its location"),
        (302, {"location": "http://[invalid"}, "valid HTTP(S) URL"),
        (302, {"location": "file:///etc/passwd"}, "HTTP(S)"),
        (200, {"content-encoding": "br"}, "unsupported content encoding"),
    ],
)
def test_response_failures_are_bounded(
    status: int,
    headers: dict[str, str],
    message: str,
) -> None:
    """Fail safely for status, redirect and encoding errors."""
    stream = _Chunks([b"not read"])
    client = _client(
        lambda request: httpx.Response(status, headers=headers, stream=stream)
    )
    with pytest.raises(FeedFetchError) as caught:
        client.fetch("https://feeds.example/feed")
    assert_that(str(caught.value)).contains(message)
    assert_that(stream.closed).is_true()
    assert_that(stream.consumed).is_zero()


@pytest.mark.parametrize("addresses", [[], ["invalid"]])
def test_invalid_dns_answers_fail_without_transport(addresses: list[str]) -> None:
    """Empty or malformed DNS answers cannot reach a connector."""

    async def resolve(host: str, port: int) -> list[str]:
        """Return the malformed resolution result."""
        del host, port
        return addresses

    handler = Mock()
    with pytest.raises(FeedFetchError, match="Feed DNS"):
        _client(handler, resolver=resolve).fetch("https://feeds.example/feed")
    assert_that(handler.call_count).is_zero()


class _WireStream(anyio.abc.ByteStream):
    """Serve a small HTTP response below the real HTTPX/httpcore transport."""

    def __init__(self) -> None:
        self.sent = bytearray()
        self.closed = False

    async def send(self, item: bytes) -> None:
        """Record the actual serialized request headers."""
        self.sent.extend(item)

    async def receive(self, max_bytes: int = 65536) -> bytes:
        """Return a complete deterministic HTTP response without a socket."""
        del max_bytes
        return b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nfeed"

    async def send_eof(self) -> None:
        """Support the stream interface; no real connection exists."""

    async def aclose(self) -> None:
        """Record transport cleanup."""
        self.closed = True


def test_real_transport_pins_connector_and_verifies_original_tls_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise production transport wiring down to TCP and TLS with fake streams."""
    stream = _WireStream()
    connector = AsyncMock(return_value=stream)
    tls = AsyncMock(return_value=stream)
    monkeypatch.setattr(anyio, "connect_tcp", connector)
    monkeypatch.setattr(anyio.streams.tls.TLSStream, "wrap", tls)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    client = FeedHttpClient(resolver=_public_dns)

    assert_that(client.fetch("https://feeds.example:8443/feed")).is_equal_to(b"feed")
    connector.assert_awaited_once_with(
        remote_host="93.184.216.34", remote_port=8443, local_host=None
    )
    assert_that(tls.await_args_list[0].kwargs["hostname"]).is_equal_to("feeds.example")
    context = tls.await_args_list[0].kwargs["ssl_context"]
    assert_that(context.check_hostname).is_true()
    assert_that(context.verify_mode).is_equal_to(ssl.CERT_REQUIRED)
    assert_that(bytes(stream.sent)).contains(b"Host: feeds.example:8443\r\n")
    assert_that(stream.closed).is_true()


def test_fetched_bytes_cannot_be_reinterpreted_as_local_filename(
    tmp_path: Path,
) -> None:
    """Feedparser must parse fetched data without treating its bytes as a filename."""
    local_feed = tmp_path / "local.xml"
    local_feed.write_text(
        "<rss version='2.0'><channel><title>Local data</title></channel></rss>"
    )
    client = _client(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(bytes(local_feed)))
    )
    assert_that(
        RSSDiscovery(fetcher=client).discover_podcast("https://feeds.example/feed")
    ).is_none()
