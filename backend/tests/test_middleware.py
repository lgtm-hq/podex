"""Integration tests for request-context logging and rate-limit middleware."""

import logging
from collections.abc import Iterator

import fakeredis
import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from starlette.requests import Request

from podex.config import Settings
from podex.main import create_app
from podex.middleware import REQUEST_ID_HEADER, RateLimitMiddleware
from podex.services.limiter import SlidingWindowRateLimiter
from podex.services.redis_limiter import RedisSlidingWindowRateLimiter


def _make_client(settings: Settings) -> TestClient:
    """Build a test client for an app configured with ``settings``.

    Args:
        settings: The settings used to construct the application.

    Returns:
        TestClient: A client bound to the configured application.
    """
    return TestClient(create_app(settings))


@pytest.fixture
def tight_client() -> Iterator[TestClient]:
    """Return a client whose limiter allows only two requests per window.

    Yields:
        TestClient: A client backed by an app with a tight rate limit.
    """
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_max_requests=2,
        rate_limit_window_seconds=60.0,
        rate_limit_exempt_paths=["/health"],
    )
    with _make_client(settings) as client:
        yield client


def test_generates_request_id_when_absent(tight_client: TestClient) -> None:
    """The middleware attaches a generated request id to every response."""
    response = tight_client.get("/api/v2/status")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.headers).contains_key(REQUEST_ID_HEADER)
    assert_that(response.headers[REQUEST_ID_HEADER]).is_not_empty()


def test_echoes_inbound_request_id(tight_client: TestClient) -> None:
    """A caller-supplied request id is echoed back unchanged."""
    response = tight_client.get(
        "/api/v2/status", headers={REQUEST_ID_HEADER: "trace-123"}
    )

    assert_that(response.headers[REQUEST_ID_HEADER]).is_equal_to("trace-123")


def test_allowed_responses_carry_rate_limit_headers(
    tight_client: TestClient,
) -> None:
    """Successful responses expose the remaining rate-limit budget."""
    response = tight_client.get("/api/v2/status")

    assert_that(response.headers["X-RateLimit-Limit"]).is_equal_to("2")
    assert_that(response.headers["X-RateLimit-Remaining"]).is_equal_to("1")
    assert_that(response.headers).contains_key("X-RateLimit-Reset")


def test_blocks_requests_over_limit_with_429(tight_client: TestClient) -> None:
    """Once the budget is exhausted the middleware returns a 429 with hints."""
    tight_client.get("/api/v2/status")
    tight_client.get("/api/v2/status")
    blocked = tight_client.get("/api/v2/status")

    assert_that(blocked.status_code).is_equal_to(429)
    assert_that(blocked.headers).contains_key("Retry-After")
    assert_that(blocked.headers["X-RateLimit-Remaining"]).is_equal_to("0")
    body = blocked.json()
    assert_that(blocked.headers["content-type"]).is_equal_to(
        "application/problem+json",
    )
    assert_that(body["status"]).is_equal_to(429)
    assert_that(body["title"]).is_equal_to("Too Many Requests")
    assert_that(body["code"]).is_equal_to("rate_limited")
    assert_that(body["detail"]).contains("Rate limit")
    assert_that(body["request_id"]).is_equal_to(
        blocked.headers[REQUEST_ID_HEADER],
    )
    assert_that(blocked.headers).contains_key(REQUEST_ID_HEADER)


def test_health_is_exempt_from_rate_limiting(tight_client: TestClient) -> None:
    """The health probe bypasses the limiter regardless of request volume."""
    for _ in range(5):
        response = tight_client.get("/health")
        assert_that(response.status_code).is_equal_to(200)

    assert_that(response.headers).does_not_contain_key("X-RateLimit-Limit")


def test_rate_limiting_can_be_disabled() -> None:
    """Disabling the limiter removes both the 429 path and its headers."""
    settings = Settings(rate_limit_enabled=False)
    with _make_client(settings) as client:
        for _ in range(10):
            response = client.get("/api/v2/status")
            assert_that(response.status_code).is_equal_to(200)

    assert_that(response.headers).does_not_contain_key("X-RateLimit-Limit")
    assert_that(response.headers).contains_key(REQUEST_ID_HEADER)


def test_client_key_falls_back_when_client_unknown() -> None:
    """A request without client info is keyed under a stable placeholder."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

    async def _app(scope: object, receive: object, send: object) -> None:
        """No-op ASGI app used only to construct the middleware."""

    middleware = RateLimitMiddleware(_app, limiter=limiter)
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})

    assert_that(middleware._client_key(request)).is_equal_to("unknown")


def test_access_log_emitted_when_handler_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled handler exceptions still produce a 500 access-log line."""
    app = create_app(Settings(rate_limit_enabled=False))

    async def _boom() -> None:
        """Route handler that always fails."""
        raise RuntimeError("boom")

    app.add_api_route("/boom", _boom, methods=["GET"])

    with TestClient(app, raise_server_exceptions=False) as client:
        with caplog.at_level(logging.INFO, logger="podex.access"):
            response = client.get("/boom", headers={REQUEST_ID_HEADER: "err-1"})

    assert_that(response.status_code).is_equal_to(500)
    records = [r for r in caplog.records if r.name == "podex.access"]
    assert_that(records).is_not_empty()
    record = records[-1]
    assert_that(record.__dict__["status_code"]).is_equal_to(500)
    assert_that(record.__dict__["request_id"]).is_equal_to("err-1")


def test_cors_exposes_rate_limit_headers_to_browsers() -> None:
    """Cross-origin responses expose the request-id and rate-limit headers."""
    settings = Settings(rate_limit_enabled=True)
    with _make_client(settings) as client:
        response = client.get(
            "/api/v2/status", headers={"Origin": "http://localhost:4321"}
        )

    exposed = response.headers.get("Access-Control-Expose-Headers", "")
    assert_that(exposed).contains("X-Request-ID")
    assert_that(exposed).contains("X-RateLimit-Limit")
    assert_that(exposed).contains("Retry-After")


def test_middleware_works_with_redis_backed_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middleware treats a Redis-backed limiter identically to the in-memory one.

    Exercises the shared-store path end-to-end (allow/deny, rate-limit headers,
    429 payload) by monkeypatching the factory to return a fakeredis-backed
    limiter. Guards against regressions in the ``RateLimiter`` protocol.
    """
    shared_limiter = RedisSlidingWindowRateLimiter(
        fakeredis.FakeStrictRedis(),
        max_requests=2,
        window_seconds=60.0,
        key_prefix="mw-test:ratelimit",
    )

    monkeypatch.setattr(
        "podex.main.build_rate_limiter", lambda _settings: shared_limiter
    )

    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_max_requests=2,
        rate_limit_window_seconds=60.0,
    )
    with TestClient(create_app(settings)) as client:
        first = client.get("/api/v2/status")
        second = client.get("/api/v2/status")
        third = client.get("/api/v2/status")

    assert_that(first.status_code).is_equal_to(200)
    assert_that(first.headers["X-RateLimit-Limit"]).is_equal_to("2")
    assert_that(second.status_code).is_equal_to(200)
    assert_that(third.status_code).is_equal_to(429)
    assert_that(third.headers).contains_key("Retry-After")
    assert_that(third.json()["code"]).is_equal_to("rate_limited")


def test_access_log_records_request_details(
    tight_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Each request emits an access log with method, path, status, and id."""
    with caplog.at_level(logging.INFO, logger="podex.access"):
        tight_client.get("/api/v2/status", headers={REQUEST_ID_HEADER: "log-1"})

    records = [r for r in caplog.records if r.name == "podex.access"]
    assert_that(records).is_not_empty()
    record = records[-1]
    assert_that(record.__dict__["method"]).is_equal_to("GET")
    assert_that(record.__dict__["path"]).is_equal_to("/api/v2/status")
    assert_that(record.__dict__["status_code"]).is_equal_to(200)
    assert_that(record.__dict__["request_id"]).is_equal_to("log-1")
    assert_that(record.__dict__["duration_ms"]).is_greater_than_or_equal_to(0.0)
