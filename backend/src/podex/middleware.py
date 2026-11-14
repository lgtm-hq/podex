"""HTTP middleware: request-context logging and per-IP rate limiting."""

import contextlib
import math
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import sentry_sdk
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from podex.api.v2.errors import (
    PROBLEM_JSON_MEDIA_TYPE,
    REQUEST_ID_HEADER,
    build_problem,
)
from podex.logging_config import get_logger
from podex.services.limiter import RateLimiter

RATE_LIMIT_MESSAGE = "Rate limit exceeded. Try again later."

_access_logger = get_logger("podex.access")

# A client-supplied request id is kept only when it is 1-128 characters of
# ASCII letters, digits, ".", "_" and "-"; anything else is replaced by a
# generated id, as if no header had been sent.
_MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")
# Upper bounds, in characters before escaping, on caller-controlled values in
# an access record. Longer values are cut and marked with ``\...``.
_MAX_LOGGED_PATH = 2048
_MAX_LOGGED_METHOD = 32
_TRUNCATION_MARKER = "\\..."

RequestResponder = Callable[[Request], Awaitable[Response]]


def _request_path(request: Request) -> str:
    """Return the decoded request path without touching the query string.

    ``request.url`` decodes the whole URL, so a query that is not valid UTF-8
    would raise; the server has already decoded the path into the scope.

    Args:
        request: The incoming request.

    Returns:
        str: The request path.
    """
    return str(request.scope.get("path", ""))


def _log_safe(value: str) -> str:
    """Escape a caller-controlled value for a single-line access record.

    Backslash, space and every character Python does not consider printable
    (C0 and C1 controls including CR, LF and tab, Unicode line and paragraph
    separators, format characters) are written as ``\\xNN``, ``\\uNNNN`` or
    ``\\UNNNNNNNN`` escapes, and a literal backslash as ``\\\\``, so the
    original value can be read back unambiguously and cannot start a new
    line or field.

    Args:
        value: Decoded text taken from the request.

    Returns:
        str: The escaped text.
    """
    escaped: list[str] = []
    for char in value:
        if char == "\\":
            escaped.append("\\\\")
        elif char.isprintable() and char != " ":
            escaped.append(char)
        elif ord(char) <= 0xFF:
            escaped.append(f"\\x{ord(char):02x}")
        elif ord(char) <= 0xFFFF:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(f"\\U{ord(char):08x}")
    return "".join(escaped)


def _bounded(value: str, limit: int) -> str:
    """Escape ``value`` for the log after cutting it to ``limit`` characters.

    The cut happens before escaping so the work stays bounded. The marker
    ``\\...`` cannot be produced by :func:`_log_safe` (it writes a literal
    backslash as ``\\\\``), so a truncated value reads back unambiguously.

    Args:
        value: Decoded text taken from the request.
        limit: Maximum number of characters to keep.

    Returns:
        str: The escaped, possibly truncated text.
    """
    if len(value) <= limit:
        return _log_safe(value)
    return _log_safe(value[:limit]) + _TRUNCATION_MARKER


def _request_id(request: Request) -> str:
    """Return the caller's request id if it is well-formed, else a new one.

    Args:
        request: The incoming request.

    Returns:
        str: A request id that is safe to echo, log and report.
    """
    supplied = request.headers.get(REQUEST_ID_HEADER)
    if (
        supplied is not None
        and len(supplied) <= _MAX_REQUEST_ID_LENGTH
        and _REQUEST_ID_PATTERN.fullmatch(supplied)
    ):
        return supplied
    return uuid4().hex


def _client_address(request: Request) -> str:
    """Return the peer address as the server resolved it, or ``-``.

    ``request.client`` reflects the server's proxy-header trust settings;
    forwarding headers are deliberately not parsed here.

    Args:
        request: The incoming request.

    Returns:
        str: ``host:port`` of the peer, or ``-`` when unknown.
    """
    if request.client is None:
        return "-"
    return f"{request.client.host}:{request.client.port}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and emit a structured access log per request.

    The middleware honours an inbound ``X-Request-ID`` header when present and
    otherwise generates one. The id is stored on ``request.state.request_id``,
    echoed back on the response, attached to error reports as a tag, and
    included in the access log alongside the method, path, status code,
    wall-clock duration, peer address and HTTP version. Query strings,
    headers, cookies and bodies are never logged.
    """

    async def dispatch(self, request: Request, call_next: RequestResponder) -> Response:
        """Wrap the downstream handler with request-id and access logging.

        Args:
            request: The incoming request being processed.
            call_next: Callable that invokes the remaining middleware/handler
                chain.

        Returns:
            Response: The downstream response with the request-id header set.
        """
        # Validated once here; the response header, problem bodies, access
        # log and error-report tag all use this value.
        request_id = _request_id(request)
        request.state.request_id = request_id
        sentry_sdk.set_tag("request_id", request_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
        except Exception:
            # Still emit the access log (as a 500) so failed requests remain
            # traceable by request id; the exception propagates to Starlette's
            # error handling unchanged.
            self._log_access_safely(
                request, status_code=status_code, start=start, request_id=request_id
            )
            raise
        status_code = response.status_code

        response.headers[REQUEST_ID_HEADER] = request_id
        self._log_access_safely(
            request, status_code=status_code, start=start, request_id=request_id
        )
        return response

    @classmethod
    def _log_access_safely(
        cls, request: Request, *, status_code: int, start: float, request_id: str
    ) -> None:
        """Emit the access record without ever affecting the response.

        Access logging must not turn a response into an error or change what
        other middleware (metrics, rate limiting) observed, so any failure is
        contained here. A minimal fallback record carries only the status
        code, never the values the full record failed on; if that fails too,
        nothing more is attempted.

        Args:
            request: The request being logged.
            status_code: The response status code (500 for unhandled errors).
            start: The ``time.perf_counter`` value captured at request start.
            request_id: The validated request id.
        """
        logged = False
        with contextlib.suppress(Exception):
            cls._log_access(
                request, status_code=status_code, start=start, request_id=request_id
            )
            logged = True
        if not logged:
            with contextlib.suppress(Exception):
                _access_logger.warning("access_log_failed status=%d", status_code)

    @staticmethod
    def _log_access(
        request: Request, *, status_code: int, start: float, request_id: str
    ) -> None:
        """Emit one structured access-log line for a completed request.

        Args:
            request: The request being logged.
            status_code: The response status code (500 for unhandled errors).
            start: The ``time.perf_counter`` value captured at request start.
            request_id: The request id associated with this request.
        """
        duration_ms = (time.perf_counter() - start) * 1000.0
        # The path is percent-decoded and caller-controlled, so it is capped
        # and escaped; the request id is already validated, escaping it is a
        # no-op kept as a guard.
        method = _bounded(request.method, _MAX_LOGGED_METHOD)
        path = _bounded(_request_path(request), _MAX_LOGGED_PATH)
        safe_request_id = _log_safe(request_id)
        client_addr = _log_safe(_client_address(request))
        http_version = _log_safe(str(request.scope.get("http_version", "-")))
        _access_logger.info(
            "%s %s %s %.2fms request_id=%s client=%s http_version=%s",
            method,
            path,
            status_code,
            duration_ms,
            safe_request_id,
            client_addr,
            http_version,
            extra={
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": safe_request_id,
                "client_addr": client_addr,
                "http_version": http_version,
            },
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce a per-client-IP request budget via a sliding-window limiter.

    Exempt paths (health probes by default) bypass the limiter entirely.
    Allowed responses carry ``X-RateLimit-*`` headers; rejected requests receive
    a ``429`` with ``Retry-After`` and the same rate-limit headers.

    Args:
        app: The wrapped ASGI application.
        limiter: The limiter instance enforcing per-client budgets. Any object
            implementing the :class:`RateLimiter` protocol (in-memory sliding
            window in dev/tests, Redis-backed sliding window in production) is
            accepted.
        exempt_paths: Paths that bypass rate limiting entirely.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        limiter: RateLimiter,
        exempt_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._exempt_paths = frozenset(exempt_paths)

    def _client_key(self, request: Request) -> str:
        """Derive the limiter key for a request.

        ``X-Forwarded-For`` is deliberately not parsed here: it is trivially
        spoofable unless validated against a trusted-proxy list. When deploying
        behind a reverse proxy, run uvicorn with ``--proxy-headers`` (and
        ``--forwarded-allow-ips``) so ``request.client`` already reflects the
        real client address by the time it reaches this middleware.

        Args:
            request: The incoming request.

        Returns:
            str: The client host when known, otherwise ``"unknown"``.
        """
        if request.client is not None:
            return request.client.host
        return "unknown"

    async def dispatch(self, request: Request, call_next: RequestResponder) -> Response:
        """Apply rate limiting before delegating to the downstream handler.

        Args:
            request: The incoming request being processed.
            call_next: Callable that invokes the remaining middleware/handler
                chain.

        Returns:
            Response: A ``429`` response when the budget is exhausted, otherwise
            the downstream response annotated with rate-limit headers.
        """
        if _request_path(request) in self._exempt_paths:
            return await call_next(request)

        decision = self._limiter.check(self._client_key(request))
        headers = {
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": str(decision.remaining),
            "X-RateLimit-Reset": str(math.ceil(decision.reset_after)),
        }

        if not decision.allowed:
            retry_after = math.ceil(decision.retry_after)
            headers["Retry-After"] = str(retry_after)
            request_id = getattr(request.state, "request_id", None)
            problem = build_problem(
                status_code=429,
                detail=RATE_LIMIT_MESSAGE,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=429,
                content=problem.model_dump(exclude_none=True),
                headers=headers,
                media_type=PROBLEM_JSON_MEDIA_TYPE,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response
