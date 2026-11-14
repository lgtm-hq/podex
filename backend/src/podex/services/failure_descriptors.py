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
from podex.services.failure_codes import FailureReason
from podex.services.feed_http import FeedFetchError

FailureSource = Literal[
    "podscripts", "rss", "spotify", "discovery", "extraction", "cleanup", "embedding"
]
MAX_DESCRIPTOR_LENGTH = 384
MAX_SUMMARY_LENGTH = 4096
LEGACY_FAILURE_WITHHELD = "legacy failure text withheld"
_FIXED_HISTORICAL_REASONS = frozenset(
    {
        "interrupted before completion",
        "Missing podcast_id",
        "Podcast not found",
        "Podcast is not active",
        "Discovery schedule disabled",
    }
)
_CLASS = r"[A-Za-z_][A-Za-z_0-9]{0,63}"
_CLASS_RE = re.compile(_CLASS)
_CODE = r"(?:SQLSTATE [0-9A-Z]{5}|SQLITE_[A-Z_]{1,40})"
_DATABASE = rf"{_CLASS}(?: \((?:{_CLASS}(?:, {_CODE})?|{_CODE})\))?"
_DATABASE_RE = re.compile(_DATABASE)
_REASONS = "|".join(re.escape(reason.value) for reason in FailureReason)
_DESCRIPTOR = (
    r"(?:podscripts|rss|spotify|discovery|extraction|cleanup|embedding): "
    rf"(?:(?:{_CLASS} -> )?{_DATABASE}(?: \(HTTP [1-5][0-9]{{2}}\))?; )?"
    rf"(?:{_REASONS})"
)
_DESCRIPTOR_RE = re.compile(_DESCRIPTOR)
_SUMMARY_RE = re.compile(rf"{_DESCRIPTOR}(?:; {_DESCRIPTOR})*")


def _safe_type_name(error: BaseException) -> str:
    """Bound runtime class metadata before it enters a diagnostic or log."""
    name = type_name(error)
    return name if _CLASS_RE.fullmatch(name) else "Exception"


def _http_status(error: BaseException) -> int | None:
    """Read status metadata from known HTTP adapters, never their messages."""
    status: object = None
    if isinstance(error, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
        status = error.response.status_code
    elif isinstance(error, (anthropic.APIStatusError, FeedFetchError)):
        status = error.status_code
    elif isinstance(error, SpotifyException):
        status = error.http_status
    return int(status) if isinstance(status, int) and 100 <= status <= 599 else None


def describe_failure(
    source: FailureSource,
    error: BaseException | None = None,
    *,
    reason: FailureReason = FailureReason.REQUEST_FAILED,
) -> str:
    """Render source, bounded exception class, protocol code and a closed reason.

    Args:
        source: Code-owned source or stage.
        error: Optional failure, whose message is never read.
        reason: Code-owned reason, including failures without an exception.

    Returns:
        A bounded diagnostic safe to persist and return to ops callers.
    """
    if error is None:
        return persisted_failure(f"{source}: {reason}")
    details = _safe_type_name(error)
    for linked in exception_tree(error):
        failure_reason = getattr(linked, "failure_reason", None)
        if isinstance(failure_reason, FailureReason):
            reason = failure_reason
        if is_database_error(linked):
            database = describe_database_error(linked)
            if not _DATABASE_RE.fullmatch(database):
                database = _safe_type_name(linked)
            details = database if linked is error else f"{details} -> {database}"
            break
        status = _http_status(linked)
        if status is not None:
            details += f" (HTTP {status})"
            break
    return persisted_failure(f"{source}: {details}; {reason}")


def persisted_failure(value: str) -> str:
    """Keep one bounded descriptor or replace unstructured runner diagnostics.

    Args:
        value: One failure returned by a discovery runner.

    Returns:
        The descriptor, or a fixed fallback without any of the input text.
    """
    if len(value) <= MAX_DESCRIPTOR_LENGTH and _DESCRIPTOR_RE.fullmatch(value):
        return value
    return "discovery: unclassified_failure"


def readable_failure(value: str | None) -> str | None:
    """Withhold unsafe historical text without modifying its stored column.

    Args:
        value: Stored run summary or scheduled work error.

    Returns:
        Valid bounded descriptors, known fixed historical reasons, null, or a
        fixed marker. A summary must consist entirely of valid descriptors.
    """
    if value is None or value in _FIXED_HISTORICAL_REASONS:
        return value
    if len(value) <= MAX_SUMMARY_LENGTH and _SUMMARY_RE.fullmatch(value):
        if all(
            len(match[0]) <= MAX_DESCRIPTOR_LENGTH
            for match in _DESCRIPTOR_RE.finditer(value)
        ):
            return value
    return LEGACY_FAILURE_WITHHELD


def _safe_database_tree(error: BaseException) -> bool:
    """Identify trees whose class metadata and driver descriptors are bounded."""
    return is_database_error(error) and all(
        is_database_error(link)
        and _DATABASE_RE.fullmatch(describe_database_error(link))
        for link in exception_tree(error)
    )


def log_failure(
    logger: logging.Logger,
    descriptor: str,
    error: BaseException,
    *,
    level: int = logging.WARNING,
    context: tuple[str | int, ...] = (),
) -> None:
    """Log objects with bounded metadata and caller-supplied operation context.

    Non-database messages can contain arbitrary credentials and bodies. Their
    stand-ins retain a safe class and traceback without unsafe messages, notes
    or chains. Database-only trees with valid metadata use the global boundary.

    Args:
        logger: Calling service's logger.
        descriptor: Rendered allowlisted failure.
        error: Original exception for the logging boundary.
        level: Existing call site severity.
        context: Fixed operation, supplied identifier and optional page number.
    """
    descriptor = persisted_failure(descriptor)
    shown = error
    if not _safe_database_tree(error):
        shown = (
            stand_in(error, descriptor)
            if _CLASS_RE.fullmatch(type_name(error))
            else Exception(descriptor)
        )
        shown.__traceback__ = error.__traceback__
    logger.log(level, "%s %s", context, descriptor, exc_info=shown)
