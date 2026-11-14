"""Public-only, bounded HTTP byte fetching shared by feed consumers."""

from __future__ import annotations

import asyncio
import ipaddress
import zlib
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from podex.config import FeedHttpSettings, get_settings
from podex.services.feed_dns import resolve_feed_host

Resolver = Callable[[str, int], Awaitable[list[str]]]

# Python 3.11 and newer minors disagree on some IANA special-purpose ranges.
# Keep these protocol/transition/documentation destinations closed on all
# supported runtimes, including allocations newer than the oldest runtime.
_SPECIAL_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "192.0.0.0/24",
        "192.88.99.0/24",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/23",
        "2002::/16",
        "3fff::/20",
    )
)


class FeedFetchError(ValueError):
    """A safe, actionable fetch failure that never includes URL credentials."""


class FeedFetcher(Protocol):
    """Injectable byte-fetching contract for synchronous discovery providers."""

    def fetch(self, url: str) -> bytes:
        """Return bounded response bytes or raise FeedFetchError."""


def public_address(value: str) -> str:
    """Validate an address, rejecting non-public and transition destinations.

    Args:
        value: Resolved or literal IP address.

    Returns:
        Canonical public address.

    Raises:
        FeedFetchError: The destination is not an ordinary public address.
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise FeedFetchError("Feed DNS returned an invalid address") from None
    if any(
        (not address.is_global, address.is_multicast, address.is_reserved, "%" in value)
    ):
        raise FeedFetchError("Feed destination must use public unicast addresses")
    if isinstance(address, ipaddress.IPv6Address) and any(
        (
            address.is_site_local,
            address.ipv4_mapped is not None,
            address.sixtofour is not None,
            address.teredo is not None,
        )
    ):
        raise FeedFetchError("Feed destination must use public unicast addresses")
    if any(address in network for network in _SPECIAL_NETWORKS):
        raise FeedFetchError("Feed destination must use public unicast addresses")
    return str(address)


def feed_url(value: str) -> httpx.URL:
    """Validate the URL without echoing credentials or other untrusted values.

    Args:
        value: Feed URL or redirect target.

    Returns:
        Normalized HTTP(S) URL.

    Raises:
        FeedFetchError: The URL is invalid or includes user information.
    """
    try:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host:
            raise FeedFetchError("Feed URL must use HTTP(S) with a host")
        if url.userinfo:
            raise FeedFetchError("Feed URL credentials are not supported")
        if "%" in url.host:
            raise FeedFetchError("Feed destination must use public unicast addresses")
    except (httpx.InvalidURL, ValueError):
        raise FeedFetchError(
            "Feed URL must be a valid HTTP(S) URL without credentials"
        ) from None
    return url.copy_with(fragment=None)


class FeedHttpClient:
    """Fetch public HTTP(S) resources with DNS pinning and finite resource limits.

    Limits and trusted DNS/transport adapters can be injected at construction.
    """

    def __init__(
        self,
        settings: FeedHttpSettings | None = None,
        *,
        resolver: Resolver = resolve_feed_host,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings().feed_http
        self.resolver = resolver
        self.transport = transport

    def fetch(self, url: str) -> bytes:
        """Fetch bytes for synchronous discovery using an isolated event loop."""
        return asyncio.run(self.fetch_async(url))

    async def fetch_async(self, url: str) -> bytes:
        """Fetch bytes within one deadline, including DNS and all redirect hops.

        Args:
            url: Initial HTTP(S) URL.

        Returns:
            Bounded, decompressed response bytes.

        Raises:
            FeedFetchError: URL, destination, HTTP, size or deadline validation failed.
        """
        try:
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                return await self._fetch(feed_url(url))
        except TimeoutError:
            raise FeedFetchError(
                "Feed total deadline exceeded; check feed availability"
            ) from None
        except httpx.TimeoutException:
            raise FeedFetchError(
                "Feed connect/read timeout; check feed availability"
            ) from None
        except (httpx.HTTPError, OSError):
            raise FeedFetchError(
                "Feed HTTP/DNS request failed; check feed availability"
            ) from None
        except zlib.error:
            raise FeedFetchError("Feed contains invalid gzip data") from None

    async def _destination(self, url: httpx.URL) -> str:
        """Validate every DNS answer and return one pinned public address."""
        try:
            address = ipaddress.ip_address(url.host)
        except ValueError:
            addresses = await self.resolver(
                url.host, url.port or (443 if url.scheme == "https" else 80)
            )
        else:
            addresses = [str(address)]
        if not addresses:
            raise FeedFetchError("Feed DNS returned no addresses")
        validated = [public_address(value) for value in addresses]
        return validated[0]

    async def _fetch(self, url: httpx.URL) -> bytes:
        """Follow validated redirects without proxy or cookie state."""
        for hop in range(self.settings.max_redirects + 1):
            address = await self._destination(url)
            timeout = httpx.Timeout(
                self.settings.read_timeout_seconds,
                connect=self.settings.connect_timeout_seconds,
            )
            transport = self.transport or httpx.AsyncHTTPTransport(
                trust_env=False, retries=0
            )
            async with transport:
                # Only the vetted numeric IP reaches the connector. HTTP Host and
                # TLS SNI/certificate verification retain the original hostname.
                # Use the transport directly to avoid HTTPX's URL-bearing INFO log.
                request = httpx.Request(
                    "GET",
                    url.copy_with(host=address),
                    headers={
                        "Host": url.netloc.decode("ascii"),
                        "Accept-Encoding": "gzip",
                    },
                    extensions={"sni_hostname": url.host, "timeout": timeout.as_dict()},
                )
                response = await transport.handle_async_request(request)
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if hop == self.settings.max_redirects:
                            raise FeedFetchError("Feed redirect limit exceeded")
                        location = response.headers.get("location")
                        if not location:
                            raise FeedFetchError(
                                "Feed redirect is missing its location"
                            )
                        try:
                            target = str(url.join(location))
                        except httpx.InvalidURL:
                            raise FeedFetchError(
                                "Feed redirect URL is invalid"
                            ) from None
                        url = feed_url(target)
                        continue
                    if not response.is_success:
                        raise FeedFetchError(f"Feed HTTP status {response.status_code}")
                    return await self._read(response)
                finally:
                    await response.aclose()
        raise FeedFetchError("Feed redirect limit exceeded")

    async def _read(self, response: httpx.Response) -> bytes:
        """Stream raw bytes and decompress with an output bound per chunk."""
        encoding = response.headers.get("content-encoding", "identity").lower().strip()
        if encoding not in {"identity", "gzip"}:
            raise FeedFetchError("Feed uses unsupported content encoding")
        decoder = (
            zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        )
        body = bytearray()
        wire_size = 0
        async for chunk in response.aiter_raw():
            wire_size += len(chunk)
            if wire_size > self.settings.max_response_bytes:
                raise FeedFetchError(
                    "Feed compressed/streaming response exceeds byte limit"
                )
            if decoder:
                chunk = decoder.decompress(
                    chunk,
                    self.settings.max_decompressed_bytes - len(body) + 1,
                )
            body.extend(chunk)
            if len(body) > self.settings.max_decompressed_bytes:
                raise FeedFetchError("Feed decompressed response exceeds byte limit")
            # Yield even for an immediately available chunk so the total deadline
            # also bounds fast streaming and decompression work.
            await asyncio.sleep(0)
        if decoder and (not decoder.eof or decoder.unused_data):
            raise FeedFetchError("Feed contains incomplete or concatenated gzip data")
        return bytes(body)
