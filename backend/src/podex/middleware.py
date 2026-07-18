"""HTTP middleware: request-context logging and per-IP rate limiting."""

import math
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from podex.api.v2.errors import PROBLEM_JSON_MEDIA_TYPE, build_problem
from podex.logging_config import get_logger
from podex.services.limiter import RateLimiter

REQUEST_ID_HEADER = "X-Request-ID"
RATE_LIMIT_MESSAGE = "Rate limit exceeded. Try again later."

_access_logger = get_logger("podex.access")

RequestResponder = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and emit a structured access log per request.

    The middleware honours an inbound ``X-Request-ID`` header when present and
    otherwise generates one. The id is stored on ``request.state.request_id``,
    echoed back on the response, and included in the access log alongside the
    method, path, status code, and wall-clock duration.
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
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
        except Exception:
            # Still emit the access log (as a 500) so failed requests remain
            # traceable by request id; the exception propagates to Starlette's
            # error handling unchanged.
            self._log_access(
                request, status_code=status_code, start=start, request_id=request_id
            )
            raise
        status_code = response.status_code

        response.headers[REQUEST_ID_HEADER] = request_id
        self._log_access(
            request, status_code=status_code, start=start, request_id=request_id
        )
        return response

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
        _access_logger.info(
            "%s %s %s %.2fms request_id=%s",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
            request_id,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": request_id,
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
