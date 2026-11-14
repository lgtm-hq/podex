"""Allowlisted ingestion diagnostics for storage, results and local logging."""

import logging
import re
from typing import Literal

import anthropic
import httpx
import httpx2
from spotipy.exceptions import SpotifyException

from podex.database_errors import (
    describe_database_error,
    exception_tree,
    is_database_error,
    stand_in,
    type_name,
)

FailureSource = Literal[
    "podscripts", "rss", "spotify", "discovery", "extraction", "cleanup"
]
FailureReason = Literal[
    "request_failed",
    "malformed_response",
    "expected_json_array",
    "missing_json_array",
    "incomplete_json_array",
    "invalid_json",
    "unclassified_failure",
]

# Runtime class names and validated codes follow the existing DB format.
_CLASS = r"[A-Za-z_][A-Za-z_0-9]*"
_DATABASE = (
    rf"{_CLASS}(?: \((?:{_CLASS}(?:, )?)?"
    r"(?:SQLSTATE [0-9A-Z]{5}|SQLITE_[A-Z_]{1,40})?\))?"
)
_DESCRIPTOR_RE = re.compile(
    r"(?:podscripts|rss|spotify|discovery|extraction|cleanup): "
    rf"(?:(?:{_CLASS} -> )?{_DATABASE}(?: \(HTTP [1-5][0-9]{{2}}\))?; )?"
    r"(?:request_failed|malformed_response|expected_json_array|missing_json_array|"
    r"incomplete_json_array|invalid_json|unclassified_failure)"
)


def describe_failure(
    source: FailureSource,
    error: BaseException | None = None,
    *,
    reason: FailureReason = "request_failed",
) -> str:
    """Render only source, exception class, validated protocol code and reason.

    Args:
        source: Code-owned source or stage.
        error: Optional failure, whose message is never read.
        reason: Code-owned reason, including failures without an exception.

    Returns:
        A diagnostic safe to persist and return to ops callers.
    """
    if error is None:
        return f"{source}: {reason}"
    details = type_name(error)
    for linked in exception_tree(error):
        if is_database_error(linked):
            database = describe_database_error(linked)
            details = database if linked is error else f"{details} -> {database}"
            break
        status: object = None
        if isinstance(linked, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
            status = linked.response.status_code
        elif isinstance(linked, anthropic.APIStatusError):
            status = linked.status_code
        elif isinstance(linked, SpotifyException):
            status = linked.http_status
        if isinstance(status, int) and 100 <= status <= 599:
            details += f" (HTTP {status})"
            break
    return f"{source}: {details}; {reason}"


def persisted_failure(value: str) -> str:
    """Keep a rendered descriptor or replace unstructured runner diagnostics.

    Args:
        value: One failure returned by a discovery runner.

    Returns:
        The descriptor, or a fixed fallback without any of the input text.
    """
    if _DESCRIPTOR_RE.fullmatch(value):
        return value
    return describe_failure("discovery", reason="unclassified_failure")


def log_failure(
    logger: logging.Logger,
    descriptor: str,
    error: BaseException,
    *,
    level: int = logging.WARNING,
) -> None:
    """Log exception objects through the existing database sanitising boundary.

    Non-database messages can carry arbitrary HTTP bodies and credentials that
    the global boundary does not scrub. A stand-in retains the original class
    and traceback, with only the descriptor as its message and no unsafe chain.
    Database exceptions retain their diagnostic tree through the global boundary.

    Args:
        logger: Calling service's logger.
        descriptor: Rendered allowlisted failure.
        error: Original exception for the logging boundary.
        level: Existing call site severity.
    """
    shown = error
    if not is_database_error(error) or not all(
        is_database_error(link) for link in exception_tree(error)
    ):
        shown = stand_in(error, descriptor)
        shown.__traceback__ = error.__traceback__
    logger.log(level, "%s", descriptor, exc_info=shown)
