"""RSS diagnostics retain code-owned failure kinds without provider text."""

import asyncio
import gzip
from datetime import UTC, datetime

import httpx
import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import FeedHttpSettings
from podex.models import IngestionRun, Podcast, ScheduledWorkItemModel
from podex.services.discovery import DiscoveryResult
from podex.services.discovery_orchestrator import DiscoveryOrchestrator
from podex.services.discovery_rss import RSSDiscovery
from podex.services.feed_http import FeedHttpClient
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    run_due_episode_discovery_work,
)

_PRIVATE_VALUE = "synthetic-private-value-812"


def _fetcher(kind: str) -> FeedHttpClient:
    """Script DNS, transport and byte limits without external connections."""

    async def resolve(_host: str, _port: int) -> list[str]:
        """Produce the DNS outcome belonging to the test case."""
        if kind == "dns_failure":
            raise OSError(_PRIVATE_VALUE)
        if kind == "total_deadline":
            await asyncio.sleep(0.02)
        return {
            "dns_no_addresses": [],
            "invalid_address": [_PRIVATE_VALUE],
            "blocked_address": ["127.0.0.1"],
        }.get(kind, ["93.184.216.34"])

    def response(request: httpx.Request) -> httpx.Response:
        """Serve controlled HTTP failures, redirects and malformed bodies."""
        if kind == "timeout":
            raise httpx.ReadTimeout(_PRIVATE_VALUE, request=request)
        if kind == "connection_failed":
            raise httpx.ConnectError(_PRIVATE_VALUE, request=request)
        if kind == "request_failed":
            raise httpx.RemoteProtocolError(_PRIVATE_VALUE, request=request)
        if kind.startswith("http_"):
            result: httpx.Response = httpx.Response(
                int(kind.removeprefix("http_")), text=_PRIVATE_VALUE
            )
            return result
        redirects = {
            "redirect_missing_location": {},
            "redirect_invalid_url": {"location": "https://[invalid"},
            "redirect_downgrade": {"location": "http://example.test/feed"},
            "redirect_limit": {"location": "/again"},
        }
        if kind in redirects:
            result = httpx.Response(302, headers=redirects[kind])
            return result
        encoding = "gzip" if kind in {"invalid_gzip", "incomplete_gzip"} else "identity"
        if kind == "unsupported_encoding":
            encoding = "unknown"
        body = (
            gzip.compress(b"fixture")[:-3]
            if kind == "incomplete_gzip"
            else _PRIVATE_VALUE.encode()
        )
        result = httpx.Response(
            200, headers={"content-encoding": encoding}, stream=httpx.ByteStream(body)
        )
        return result

    fetcher: FeedHttpClient = FeedHttpClient(
        settings=FeedHttpSettings(
            total_timeout_seconds=0.005 if kind == "total_deadline" else 2,
            max_response_bytes=1 if kind == "response_too_large" else 100,
            max_decompressed_bytes=(
                1 if kind == "decompressed_response_too_large" else 100
            ),
            max_redirects=1,
        ),
        resolver=resolve,
        transport=httpx.MockTransport(response),
    )
    return fetcher


@pytest.mark.parametrize(
    "kind",
    [
        "http_404",
        "http_429",
        "http_500",
        "dns_failure",
        "dns_no_addresses",
        "invalid_address",
        "blocked_address",
        "total_deadline",
        "timeout",
        "connection_failed",
        "request_failed",
        "response_too_large",
        "decompressed_response_too_large",
        "unsupported_encoding",
        "invalid_gzip",
        "incomplete_gzip",
        "redirect_missing_location",
        "redirect_invalid_url",
        "redirect_downgrade",
        "redirect_limit",
        "invalid_url",
        "url_credentials",
        "unsupported_url",
    ],
)
def test_rss_failures_keep_reason_and_status_in_storage_and_logs(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    """Drive real RSS fetch failures through scheduling and persisted results."""
    url = {
        "invalid_url": "https://%invalid/feed",
        "url_credentials": str(
            httpx.URL(
                "https://example.test/feed", username="fixture", password=_PRIVATE_VALUE
            ),
        ),
        "unsupported_url": "ftp://example.test/feed",
    }.get(kind, "https://example.test/feed")
    db_session.add(
        Podcast(name="Fixture", slug="fixture", status="active", rss_url=url)
    )
    db_session.commit()
    plan_recurring_discovery_work(db=db_session, now=datetime(2026, 5, 10, tzinfo=UTC))

    def runner(db: Session, podcast: Podcast) -> DiscoveryResult:
        """Use the real RSS provider while isolating the network adapters."""
        with DiscoveryOrchestrator(db) as orchestrator:
            monkeypatch.setattr(
                orchestrator, "_sources", {"rss": RSSDiscovery(_fetcher(kind))}
            )
            return orchestrator.discover_for_podcast(podcast)

    results = run_due_episode_discovery_work(db=db_session, runner=runner)
    db_session.commit()
    expected = (
        f"rss: FeedFetchError (HTTP {kind.removeprefix('http_')}); http_status"
        if kind.startswith("http_")
        else f"rss: FeedFetchError; {kind}"
    )
    assert_that(results[0].errors).is_equal_to([expected])
    assert_that(db_session.query(IngestionRun).one().error_summary).is_equal_to(
        expected
    )
    assert_that(
        db_session.query(ScheduledWorkItemModel).one().error_message
    ).is_equal_to(expected)
    assert_that(caplog.text).contains(expected).does_not_contain(_PRIVATE_VALUE)
