"""Integration tests for request-context logging and rate-limit middleware."""

import asyncio
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import fakeredis
import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from starlette.requests import Request

from podex.config import RateLimitSettings, Settings
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


@pytest.fixture(name="tight_client")
def tight_client_fixture() -> Iterator[TestClient]:
    """Return a client whose limiter allows only two requests per window.

    Yields:
        TestClient: A client backed by an app with a tight rate limit.
    """
    settings = Settings(
        rate_limit=RateLimitSettings(
            enabled=True,
            max_requests=2,
            window_seconds=60.0,
            exempt_paths=["/health"],
        ),
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
    settings = Settings(rate_limit=RateLimitSettings(enabled=False))
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
        del scope, receive, send

    middleware = RateLimitMiddleware(_app, limiter=limiter)
    request = Request({"type": "http", "headers": [], "method": "GET", "path": "/"})

    # Direct unit coverage of the private normalization/protocol boundary.
    # pylint: disable-next=protected-access
    assert_that(middleware._client_key(request)).is_equal_to("unknown")


def test_access_log_emitted_when_handler_raises(
    failing_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled handler exceptions still produce a 500 access-log line."""
    with caplog.at_level(logging.INFO, logger="podex.access"):
        response = failing_client.get("/boom", headers={REQUEST_ID_HEADER: "err-1"})

    assert_that(response.status_code).is_equal_to(500)
    records = [r for r in caplog.records if r.name == "podex.access"]
    assert_that(records).is_not_empty()
    record = records[-1]
    assert_that(record.__dict__["status_code"]).is_equal_to(500)
    assert_that(record.__dict__["request_id"]).is_equal_to("err-1")


def test_cors_exposes_rate_limit_headers_to_browsers() -> None:
    """Cross-origin responses expose the request-id and rate-limit headers."""
    settings = Settings(rate_limit=RateLimitSettings(enabled=True))
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
        rate_limit=RateLimitSettings(
            enabled=True,
            max_requests=2,
            window_seconds=60.0,
        ),
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


def _access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the application access-log records captured so far.

    Args:
        caplog: The pytest log-capture fixture.

    Returns:
        list[logging.LogRecord]: Records emitted by ``podex.access``.
    """
    return [record for record in caplog.records if record.name == "podex.access"]


@pytest.mark.parametrize(
    ("origin", "expected_status"),
    [("http://localhost:4321", 200), ("https://untrusted.example", 400)],
    ids=["allowed-origin", "disallowed-origin"],
)
def test_access_log_records_cors_preflight(
    caplog: pytest.LogCaptureFixture,
    origin: str,
    expected_status: int,
) -> None:
    """Preflights answered by the CORS layer still produce an access-log line."""
    settings = Settings(rate_limit=RateLimitSettings(enabled=False))
    with _make_client(settings) as client, caplog.at_level(
        logging.INFO, logger="podex.access"
    ):
        response = client.options(
            "/api/v2/status",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )

    assert_that(response.status_code).is_equal_to(expected_status)
    records = _access_records(caplog)
    assert_that(records).is_length(1)
    assert_that(records[0].__dict__["method"]).is_equal_to("OPTIONS")
    assert_that(records[0].__dict__["path"]).is_equal_to("/api/v2/status")
    assert_that(records[0].__dict__["status_code"]).is_equal_to(expected_status)
    assert_that(records[0].__dict__["duration_ms"]).is_greater_than_or_equal_to(0.0)


def test_access_log_records_unrouted_path_without_query(
    tight_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 404 is logged by path only; its query string never reaches the log."""
    with caplog.at_level(logging.INFO, logger="podex.access"):
        response = tight_client.get(
            "/api/v2/does-not-exist",
            params={"code": "query-code-123", "state": "query-state-456"},
        )

    assert_that(response.status_code).is_equal_to(404)
    records = _access_records(caplog)
    assert_that(records).is_length(1)
    record = records[0]
    assert_that(record.__dict__["method"]).is_equal_to("GET")
    assert_that(record.__dict__["path"]).is_equal_to("/api/v2/does-not-exist")
    assert_that(record.__dict__["status_code"]).is_equal_to(404)
    assert_that(record.getMessage()).does_not_contain("query-code-123")
    assert_that(record.getMessage()).does_not_contain("query-state-456")
    assert_that(record.getMessage()).does_not_contain("?")


_RAW_CLIENT = ("203.0.113.7", 51234)


@dataclass
class _RawResponse:
    """What a raw ASGI call sent back to the server."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)


def _raw_asgi_request(
    *,
    method: str,
    path: str,
    query_string: bytes,
    headers: tuple[tuple[str, str], ...] = (),
    client: tuple[str, int] | None = _RAW_CLIENT,
) -> _RawResponse:
    """Call the application directly with a hand-built ASGI scope.

    ``TestClient`` normalises the query string, so a non-UTF-8 byte only
    reaches the application through a raw scope, as a server would pass it.

    Args:
        method: HTTP method.
        path: Decoded request path.
        query_string: Raw query bytes exactly as received.
        headers: Request headers.
        client: The peer address the server resolved, if any.

    Returns:
        _RawResponse: The response status and headers sent by the app.
    """
    app = create_app(Settings(rate_limit=RateLimitSettings(enabled=True)))
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "root_path": "",
        "query_string": query_string,
        "headers": [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in (("host", "testserver"), *headers)
        ],
        "client": client,
        "server": ("testserver", 80),
    }
    response = _RawResponse()

    async def receive() -> dict[str, Any]:
        """Deliver an empty request body."""
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        """Record the response start message."""
        if message["type"] == "http.response.start":
            response.status = message["status"]
            response.headers = {
                name.decode("latin-1"): value.decode("latin-1")
                for name, value in message.get("headers", [])
            }

    asyncio.run(app(scope, receive, send))
    return response


_PREFLIGHT = (
    ("origin", "http://localhost:4321"),
    ("access-control-request-method", "GET"),
)
_BLOCKED_PREFLIGHT = (
    ("origin", "https://untrusted.example"),
    ("access-control-request-method", "GET"),
)


@pytest.mark.parametrize(
    ("method", "path", "headers", "expected_status"),
    [
        ("GET", "/api/v2/status", (), 200),
        ("GET", "/health", (), 200),
        ("GET", "/api/v2/does-not-exist", (), 404),
        ("OPTIONS", "/api/v2/status", _PREFLIGHT, 200),
        ("OPTIONS", "/api/v2/status", _BLOCKED_PREFLIGHT, 400),
    ],
    ids=["route", "exempt-route", "not-found", "preflight", "blocked-preflight"],
)
def test_non_utf8_query_is_logged_by_path_without_failing(
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    headers: tuple[tuple[str, str], ...],
    expected_status: int,
) -> None:
    """A raw ``0xFF`` query byte neither breaks the middleware nor is logged."""
    clean = _raw_asgi_request(
        method=method, path=path, query_string=b"ok=1", headers=headers
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="podex.access"):
        response = _raw_asgi_request(
            method=method, path=path, query_string=b"bad=\xff", headers=headers
        )

    assert_that(response.status).is_equal_to(expected_status)
    assert_that(clean.status).is_equal_to(expected_status)
    # Same CORS (and other) headers as for a well-formed query.
    for name, value in clean.headers.items():
        if name.startswith("access-control-"):
            assert_that(response.headers).contains_entry({name: value})
    records = _access_records(caplog)
    assert_that(records).is_length(1)
    record = records[0]
    assert_that(record.__dict__["method"]).is_equal_to(method)
    assert_that(record.__dict__["path"]).is_equal_to(path)
    assert_that(record.__dict__["status_code"]).is_equal_to(expected_status)
    assert_that(record.getMessage()).does_not_contain("bad=")
    assert_that(record.getMessage()).does_not_contain("?")


def test_access_log_records_client_address_and_http_version(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The access record carries the resolved peer and HTTP version only."""
    with caplog.at_level(logging.INFO, logger="podex.access"):
        _raw_asgi_request(
            method="GET",
            path="/api/v2/status",
            query_string=b"code=query-code-123",
            headers=(("cookie", "podex_session=cookie-789"),),
        )

    records = _access_records(caplog)
    assert_that(records).is_length(1)
    record = records[0]
    assert_that(record.__dict__["client_addr"]).is_equal_to("203.0.113.7:51234")
    assert_that(record.__dict__["http_version"]).is_equal_to("1.1")
    message = record.getMessage()
    assert_that(message).contains("client=203.0.113.7:51234", "http_version=1.1")
    for leaked in ("query-code-123", "cookie-789", "podex_session"):
        assert_that(message).does_not_contain(leaked)
        assert_that(repr(record.__dict__)).does_not_contain(leaked)


def test_access_log_marks_unknown_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A scope without a peer address is logged with a placeholder."""
    with caplog.at_level(logging.INFO, logger="podex.access"):
        _raw_asgi_request(
            method="GET", path="/api/v2/status", query_string=b"", client=None
        )

    records = _access_records(caplog)
    assert_that(records).is_length(1)
    assert_that(records[0].__dict__["client_addr"]).is_equal_to("-")
