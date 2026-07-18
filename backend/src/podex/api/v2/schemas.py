"""Shared v2 API conventions: pagination inputs, page envelope, error envelope.

Every list endpoint under ``/api/v2`` accepts the same ``limit``/``offset``
query parameters (see :class:`PaginationParams`) and returns a
:class:`Page` envelope so that clients can page uniformly and know the total
count without an extra round-trip. Errors returned by the v2 surface are
wrapped in :class:`ErrorResponse` with a machine-readable ``code`` and the
request id, so callers can correlate failures with server logs.
"""

from __future__ import annotations

from typing import Annotated, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200

ItemT = TypeVar("ItemT")


class PaginationParams(BaseModel):
    """Normalised pagination inputs shared by every v2 list endpoint.

    Attributes:
        limit: Maximum number of items to return; clamped to
            ``[1, MAX_LIMIT]``.
        offset: Number of items to skip before returning results;
            non-negative.
    """

    model_config = ConfigDict(frozen=True)

    limit: Annotated[
        int,
        Field(
            ge=1,
            le=MAX_LIMIT,
            description="Maximum number of items to return.",
        ),
    ] = DEFAULT_LIMIT
    offset: Annotated[
        int,
        Field(ge=0, description="Number of items to skip."),
    ] = 0


def pagination_params(
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_LIMIT,
            description="Maximum number of items to return.",
        ),
    ] = DEFAULT_LIMIT,
    offset: Annotated[
        int,
        Query(ge=0, description="Number of items to skip."),
    ] = 0,
) -> PaginationParams:
    """FastAPI dependency that parses shared ``limit``/``offset`` query params.

    Args:
        limit: The requested page size, validated by FastAPI to be within
            ``[1, MAX_LIMIT]``.
        offset: The number of items to skip, validated to be non-negative.

    Returns:
        The parsed :class:`PaginationParams` instance.
    """
    return PaginationParams(limit=limit, offset=offset)


class Page(BaseModel, Generic[ItemT]):
    """A single page of results plus paging metadata.

    Attributes:
        items: The items belonging to this page, in the endpoint's natural
            order.
        total: The total number of items matching the query, ignoring
            pagination.
        limit: The ``limit`` that produced this page.
        offset: The ``offset`` that produced this page.
    """

    items: list[ItemT]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ErrorDetail(BaseModel):
    """A single field-level validation problem.

    Attributes:
        loc: The path to the offending field within the request payload.
        msg: A human-readable description of the problem.
        type: The pydantic/FastAPI error type discriminator.
    """

    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    """The ``error`` object embedded in an :class:`ErrorResponse`.

    Attributes:
        code: A stable, machine-readable error code (e.g. ``"not_found"``).
        message: Human-readable summary safe to surface to end users.
        request_id: The request id assigned by the request-context middleware,
            for cross-referencing with server logs.
        details: Optional field-level breakdown, populated for validation
            failures.
    """

    code: str
    message: str
    request_id: str | None = None
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    """The uniform error envelope returned by ``/api/v2`` failures.

    Attributes:
        error: The :class:`ErrorBody` describing the failure.
    """

    error: ErrorBody
