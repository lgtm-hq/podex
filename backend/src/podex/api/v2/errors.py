"""Exception handlers that normalise ``/api/v2`` error bodies.

The v2 surface returns an RFC 9457 problem-details body (see
:class:`podex.api.v2.schemas.ProblemDetails`, served as
``application/problem+json``) for every failure mode: 404 from missing
resources, 422 from request validation, 429 from the rate limiter, and 500
from unhandled server errors. The handlers preserve the original status code
and any response headers that FastAPI or middleware attached
(``WWW-Authenticate``, ``Retry-After``, ``X-RateLimit-*``, ...) while
replacing the body with the standardised shape.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from podex.api.v2.schemas import ErrorDetail, ProblemDetails

PROBLEM_JSON_MEDIA_TYPE: Final = "application/problem+json"
"""The RFC 9457 media type every v2 error response is served with."""

PROBLEM_TYPE_BASE: Final = "https://podex.example/problems/"
"""Base URI for problem ``type`` members; the code slug is appended."""

STATUS_CODE_TO_ERROR_CODE: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_410_GONE: "gone",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "unprocessable_entity",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_server_error",
    status.HTTP_502_BAD_GATEWAY: "bad_gateway",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
    status.HTTP_504_GATEWAY_TIMEOUT: "gateway_timeout",
}


def error_code_for_status(status_code: int) -> str:
    """Return the canonical error ``code`` string for an HTTP status code.

    Args:
        status_code: The response status code being reported.

    Returns:
        The registered code, or a generic ``"http_<code>"`` fallback.
    """
    return STATUS_CODE_TO_ERROR_CODE.get(status_code, f"http_{status_code}")


def _title_for_status(status_code: int) -> str:
    """Return the HTTP reason phrase for ``status_code``.

    Args:
        status_code: The response status code being reported.

    Returns:
        The standard reason phrase, or ``"Error"`` for unregistered codes.
    """
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def build_problem(
    *,
    status_code: int,
    detail: str | None,
    code: str | None = None,
    request_id: str | None = None,
    errors: list[ErrorDetail] | None = None,
) -> ProblemDetails:
    """Construct the RFC 9457 body shared by handlers and middleware.

    Args:
        status_code: The HTTP status of this occurrence.
        detail: Occurrence-specific human-readable explanation, or ``None``
            when the ``title`` alone suffices.
        code: An override for the machine-readable code; defaults to the
            canonical code for ``status_code``.
        request_id: The request id to echo for log correlation.
        errors: Optional per-field validation breakdown.

    Returns:
        The populated :class:`ProblemDetails` model.
    """
    resolved_code = code or error_code_for_status(status_code)
    return ProblemDetails(
        type=f"{PROBLEM_TYPE_BASE}{resolved_code}",
        title=_title_for_status(status_code),
        status=status_code,
        detail=detail or None,
        code=resolved_code,
        request_id=request_id,
        errors=errors,
    )


def _request_id(request: Request) -> str | None:
    """Return the request id set by the request-context middleware, if any."""
    return getattr(request.state, "request_id", None)


def build_error_response(
    *,
    status_code: int,
    detail: str | None,
    request: Request,
    code: str | None = None,
    errors: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Construct a problem-details :class:`JSONResponse`.

    Args:
        status_code: The HTTP status to return.
        detail: The occurrence-specific human-readable explanation.
        request: The active request (used to source the request id).
        code: An override for the machine-readable code; defaults to the
            canonical code for ``status_code``.
        errors: Optional per-field validation details.
        headers: Optional response headers to preserve on the reply.

    Returns:
        A JSON response carrying the RFC 9457 body with the
        ``application/problem+json`` media type.
    """
    payload = build_problem(
        status_code=status_code,
        detail=detail,
        code=code,
        request_id=_request_id(request),
        errors=errors,
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(exclude_none=True),
        headers=headers,
        media_type=PROBLEM_JSON_MEDIA_TYPE,
    )


async def http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """Wrap ``HTTPException.detail`` into the problem-details body."""
    raw: Any = exc.detail
    detail: str | None
    errors: list[ErrorDetail] | None = None
    code: str | None = None
    if isinstance(raw, dict):
        detail = str(raw.get("message") or raw.get("detail") or "") or None
        raw_code = raw.get("code")
        if isinstance(raw_code, str):
            code = raw_code
        raw_details = raw.get("details")
        if isinstance(raw_details, list):
            errors = [
                ErrorDetail.model_validate(item)
                for item in raw_details
                if isinstance(item, dict)
            ]
    else:
        detail = str(raw) if raw is not None else None
    return build_error_response(
        status_code=exc.status_code,
        detail=detail,
        request=request,
        code=code,
        errors=errors,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Wrap ``RequestValidationError`` into the problem-details body."""
    raw_errors = jsonable_encoder(exc.errors())
    errors: list[ErrorDetail] = []
    for item in raw_errors:
        if not isinstance(item, dict):
            continue
        loc_value = item.get("loc") or []
        loc = [part for part in loc_value if isinstance(part, str | int)]
        errors.append(
            ErrorDetail(
                loc=loc,
                msg=str(item.get("msg", "")),
                type=str(item.get("type", "")),
            ),
        )
    return build_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Request validation failed.",
        request=request,
        errors=errors,
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return an opaque 500 problem body for otherwise unhandled exceptions."""
    del exc
    return build_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Internal server error.",
        request=request,
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register the standardised exception handlers on ``app``.

    Args:
        app: The FastAPI application to configure.
    """
    # Starlette's add_exception_handler is typed for a narrower handler
    # signature than FastAPI actually accepts (it forwards FastAPI-typed
    # exceptions/requests through unchanged). Suppress the resulting arg-type
    # noise locally while also silencing the unused-ignore warning that the
    # CI mypy image (which resolves fastapi/starlette to Any without runtime
    # deps installed) would otherwise emit for the same lines.
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type, unused-ignore]
    app.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,  # type: ignore[arg-type, unused-ignore]
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
