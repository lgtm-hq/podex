"""HTTP middleware: request-context logging and per-IP rate limiting."""

import math
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from podex.logging_config import get_logger
from podex.services.limiter import SlidingWindowRateLimiter

REQUEST_ID_HEADER = "X-Request-ID"

_access_logger = get_logger("podex.access")

RequestResponder = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and emit a structured access log per request.

    The middleware honours an inbound ``X-Request-ID`` header when present and
    otherwise generates one. The id is stored on ``request.state.request_id``,
    echoed back on the response, and included in the access log alongside the
    method, path, status code, and wall-clock duration.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponder
    ) -> Response:
        """Wrap the downstream handler with request-id and access logging.

        Args:
            request: The incoming request being processed.
            call_next: Callable that invokes the remaining middleware/handler
                chain.

        Returns:
            Response: The downstream response with the request-id header set.
        """
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000.0

        response.headers[REQUEST_ID_HEADER] = request_id
        _access_logger.info(
            "%s %s %s %.2fms request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": request_id,
            },
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce a per-client-IP request budget via a sliding-window limiter.

    Exempt paths (health probes by default) bypass the limiter entirely.
    Allowed responses carry ``X-RateLimit-*`` headers; rejected requests receive
    a ``429`` with ``Retry-After`` and the same rate-limit headers.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        limiter: SlidingWindowRateLimiter,
        exempt_paths: tuple[str, ...] = (),
    ) -> None:
        """Initialise the middleware.

        Args:
            app: The wrapped ASGI application.
            limiter: The limiter instance enforcing per-client budgets.
            exempt_paths: Paths that bypass rate limiting entirely.
        """
        super().__init__(app)
        self._limiter = limiter
        self._exempt_paths = frozenset(exempt_paths)

    def _client_key(self, request: Request) -> str:
        """Derive the limiter key for a request.

        Args:
            request: The incoming request.

        Returns:
            str: The client host when known, otherwise ``"unknown"``.
        """
        if request.client is not None:
            return request.client.host
        return "unknown"

    async def dispatch(
        self, request: Request, call_next: RequestResponder
    ) -> Response:
        """Apply rate limiting before delegating to the downstream handler.

        Args:
            request: The incoming request being processed.
            call_next: Callable that invokes the remaining middleware/handler
                chain.

        Returns:
            Response: A ``429`` response when the budget is exhausted, otherwise
            the downstream response annotated with rate-limit headers.
        """
        if request.url.path in self._exempt_paths:
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
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers=headers,
            )

        response = await call_next(request)
        for name, value in headers.items():
            response.headers[name] = value
        return response
