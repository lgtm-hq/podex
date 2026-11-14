"""Public-only, bounded HTTP byte fetching shared by feed consumers."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import zlib
from collections.abc import Awaitable, Callable
from typing import Protocol

import httpx

from podex.config import FeedHttpSettings, get_settings
from podex.services.failure_codes import FailureReason
from podex.services.feed_dns import FeedDnsError, resolve_feed_host
from podex.services.feed_response import FeedResponse

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
    """A safe, actionable fetch failure with a code-owned reason and status.

    Args:
        message: Fixed diagnostic chosen by the fetch boundary.
        reason: Explicit failure kind selected where the error is raised.
        status_code: HTTP response status, when applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: FailureReason,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_reason = reason
        self.status_code = status_code


class FeedFetcher(Protocol):
    """Injectable byte-fetching contract for synchronous discovery providers."""

    def fetch(self, url: str) -> FeedResponse:
        """Return bounded bytes and parser metadata or raise FeedFetchError."""


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
        raise FeedFetchError(
            "Feed DNS returned an invalid address", reason=FailureReason.INVALID_ADDRESS
        ) from None
    if any(
        (not address.is_global, address.is_multicast, address.is_reserved, "%" in value)
    ):
        raise FeedFetchError(
            "Feed destination must use public unicast addresses",
            reason=FailureReason.BLOCKED_ADDRESS,
        )
    if isinstance(address, ipaddress.IPv6Address) and any(
        (
            address.is_site_local,
            address.ipv4_mapped is not None,
            address.sixtofour is not None,
            address.teredo is not None,
        )
    ):
        raise FeedFetchError(
            "Feed destination must use public unicast addresses",
            reason=FailureReason.BLOCKED_ADDRESS,
        )
    if any(address in network for network in _SPECIAL_NETWORKS):
        raise FeedFetchError(
            "Feed destination must use public unicast addresses",
            reason=FailureReason.BLOCKED_ADDRESS,
        )
    return str(address)


def feed_url(
    value: str,
    *,
    invalid_reason: FailureReason | None = None,
) -> httpx.URL:
    """Validate the URL without echoing credentials or other untrusted values.

    Args:
        value: Feed URL or redirect target.
        invalid_reason: Optional code-owned context for malformed redirect URLs.

    Returns:
        Normalized HTTP(S) URL.

    Raises:
        FeedFetchError: The URL is invalid or includes user information.
    """
    try:
        url = httpx.URL(value)
        if url.scheme not in {"http", "https"} or not url.host:
            raise FeedFetchError(
                "Feed URL must use HTTP(S) with a host",
                reason=invalid_reason or FailureReason.UNSUPPORTED_URL,
            )
        if url.userinfo:
            raise FeedFetchError(
                "Feed URL credentials are not supported",
                reason=FailureReason.URL_CREDENTIALS,
            )
        if "%" in url.host:
            # A zone identifier marks a scoped IPv6 literal; any other percent
            # sign is a host that failed to parse.
            if ":" in url.host:
                raise FeedFetchError(
                    "Feed destination must use public unicast addresses",
                    reason=FailureReason.BLOCKED_ADDRESS,
                )
            raise FeedFetchError(
                "Feed URL must be a valid HTTP(S) URL without credentials",
                reason=invalid_reason or FailureReason.INVALID_URL,
            )
    except FeedFetchError:
        raise
    except (httpx.InvalidURL, ValueError):
        raise FeedFetchError(
            "Feed URL must be a valid HTTP(S) URL without credentials",
            reason=invalid_reason or FailureReason.INVALID_URL,
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

    def fetch(self, url: str) -> FeedResponse:
        """Fetch bytes for synchronous discovery using an isolated event loop."""
        return asyncio.run(self.fetch_async(url))

    async def fetch_async(self, url: str) -> FeedResponse:
        """Fetch bytes within one deadline, including DNS and all redirect hops.

        Args:
            url: Initial HTTP(S) URL.

        Returns:
            Bounded, decompressed bytes with the final URL and content type.

        Raises:
            FeedFetchError: URL, destination, HTTP, size or deadline validation failed.
        """
        failure = FeedFetchError(
            "Feed HTTP/DNS request failed; check feed availability",
            reason=FailureReason.REQUEST_FAILED,
        )
        try:
            async with asyncio.timeout(self.settings.total_timeout_seconds):
                return await self._fetch(feed_url(url))
        except TimeoutError:
            failure = FeedFetchError(
                "Feed total deadline exceeded; check feed availability",
                reason=FailureReason.TOTAL_DEADLINE,
            )
        except httpx.TimeoutException:
            failure = FeedFetchError(
                "Feed connect/read timeout; check feed availability",
                reason=FailureReason.TIMEOUT,
            )
        except FeedDnsError as error:
            failure = FeedFetchError(
                "Feed HTTP/DNS request failed; check feed availability",
                reason=error.failure_reason,
            )
        except (httpx.HTTPError, OSError):
            failure = FeedFetchError(
                "Feed HTTP/DNS request failed; check feed availability",
                reason=FailureReason.REQUEST_FAILED,
            )
        except zlib.error:
            failure = FeedFetchError(
                "Feed contains invalid gzip data", reason=FailureReason.INVALID_GZIP
            )

        raise failure

    async def _destinations(self, url: httpx.URL) -> list[str]:
        """Validate every DNS answer before returning any connection candidates."""
        address: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        with contextlib.suppress(ValueError):
            address = ipaddress.ip_address(url.host)
        if address is None:
            failure: FeedDnsError | None = None
            addresses: list[str] = []
            try:
                addresses = await self.resolver(
                    url.host, url.port or (443 if url.scheme == "https" else 80)
                )
            except FeedDnsError:
                raise
            except OSError:
                failure = FeedDnsError(
                    "Feed DNS resolution failed",
                    reason=FailureReason.DNS_FAILURE,
                )
            if failure is not None:
                raise failure
        else:
            addresses = [str(address)]
        if not addresses:
            raise FeedFetchError(
                "Feed DNS returned no addresses", reason=FailureReason.DNS_NO_ADDRESSES
            )
        return [public_address(value) for value in addresses]

    async def _request(
        self, url: httpx.URL, transport: httpx.AsyncBaseTransport
    ) -> httpx.Response:
        """Try vetted addresses in order, retrying only connection establishment."""
        addresses = await self._destinations(url)
        timeout = httpx.Timeout(
            self.settings.read_timeout_seconds,
            connect=self.settings.connect_timeout_seconds,
        )
        for address in addresses:
            # Pin the connector while retaining Host and TLS verification identity.
            # Direct transport use also avoids HTTPX's URL-bearing INFO log.
            request = httpx.Request(
                "GET",
                url.copy_with(host=address),
                headers={
                    "Host": url.netloc.decode("ascii"),
                    "Accept-Encoding": "gzip",
                    "User-Agent": "podex-feed-fetcher/1.0",
                    "Accept": "application/rss+xml, application/atom+xml, "
                    "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.1",
                },
                extensions={"sni_hostname": url.host, "timeout": timeout.as_dict()},
            )
            try:
                return await transport.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                continue
        raise FeedFetchError(
            "Feed connection failed; check feed availability",
            reason=FailureReason.CONNECTION_FAILED,
        )

    def _redirect(self, url: httpx.URL, response: httpx.Response) -> httpx.URL:
        """Validate redirect syntax and policy without exposing URL details."""
        location = response.headers.get("location")
        if not location:
            raise FeedFetchError(
                "Feed redirect is missing its location",
                reason=FailureReason.REDIRECT_MISSING_LOCATION,
            )
        failure: FeedFetchError | None = None
        try:
            target = feed_url(
                str(url.join(location)),
                invalid_reason=FailureReason.REDIRECT_INVALID_URL,
            )
        except httpx.InvalidURL:
            failure = FeedFetchError(
                "Feed redirect URL is invalid",
                reason=FailureReason.REDIRECT_INVALID_URL,
            )
        else:
            failure = None
        if failure is not None:
            raise failure
        if url.scheme == "https" and target.scheme == "http":
            raise FeedFetchError(
                "Feed redirect cannot downgrade HTTPS to HTTP",
                reason=FailureReason.REDIRECT_DOWNGRADE,
            )
        return target

    async def _fetch(self, url: httpx.URL) -> FeedResponse:
        """Follow validated redirects without proxy or cookie state."""
        for hop in range(self.settings.max_redirects + 1):
            transport = self.transport or httpx.AsyncHTTPTransport(
                trust_env=False, retries=0
            )
            async with transport:
                response = await self._request(url, transport)
                try:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if hop == self.settings.max_redirects:
                            raise FeedFetchError(
                                "Feed redirect limit exceeded",
                                reason=FailureReason.REDIRECT_LIMIT,
                            )
                        url = self._redirect(url, response)
                        continue
                    if not response.is_success:
                        raise FeedFetchError(
                            f"Feed HTTP status {response.status_code}",
                            reason=FailureReason.HTTP_STATUS,
                            status_code=response.status_code,
                        )
                    return FeedResponse(
                        body=await self._read(response),
                        final_url=str(url),
                        content_type=response.headers.get("content-type", ""),
                    )
                finally:
                    await response.aclose()
        raise FeedFetchError(
            "Feed redirect limit exceeded", reason=FailureReason.REDIRECT_LIMIT
        )

    async def _read(self, response: httpx.Response) -> bytes:
        """Stream raw bytes and decompress with an output bound per chunk."""
        encoding = response.headers.get("content-encoding", "identity").lower().strip()
        if encoding not in {"identity", "gzip"}:
            raise FeedFetchError(
                "Feed uses unsupported content encoding",
                reason=FailureReason.UNSUPPORTED_ENCODING,
            )
        decoder = (
            zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
        )
        body = bytearray()
        wire_size = 0
        async for chunk in response.aiter_raw():
            wire_size += len(chunk)
            if wire_size > self.settings.max_response_bytes:
                raise FeedFetchError(
                    "Feed compressed/streaming response exceeds byte limit",
                    reason=FailureReason.RESPONSE_TOO_LARGE,
                )
            if decoder:
                chunk = decoder.decompress(
                    chunk,
                    self.settings.max_decompressed_bytes - len(body) + 1,
                )
            body.extend(chunk)
            if len(body) > self.settings.max_decompressed_bytes:
                raise FeedFetchError(
                    "Feed decompressed response exceeds byte limit",
                    reason=FailureReason.DECOMPRESSED_RESPONSE_TOO_LARGE,
                )
            # Yield even for an immediately available chunk so the total deadline
            # also bounds fast streaming and decompression work.
            await asyncio.sleep(0)
        if decoder and (not decoder.eof or decoder.unused_data):
            raise FeedFetchError(
                "Feed contains incomplete or concatenated gzip data",
                reason=FailureReason.INCOMPLETE_GZIP,
            )
        return bytes(body)
