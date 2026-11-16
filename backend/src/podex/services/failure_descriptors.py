"""Allowlisted ingestion diagnostics for storage, results and local logging."""

import contextlib
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
MAX_CONTEXT_LENGTH = 64
MAX_CONTEXT_ITEMS = 3
MAX_NAMESPACE_LENGTH = 128
INVALID_CONTEXT = "[invalid context]"
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
_NAMESPACE_RE = re.compile(rf"{_CLASS}(?:\.{_CLASS})*")
_QUALNAME_RE = re.compile(rf"{_CLASS}(?:\.(?:{_CLASS}|<locals>))*")
_CONTEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
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


def _metadata_attribute(value: object, name: str) -> object:
    """Read diagnostic metadata without letting a failing property escape."""
    result: object = None
    with contextlib.suppress(Exception):
        result = getattr(value, name, None)
    return result


def _safe_type_name(error: BaseException) -> str:
    """Bound runtime class metadata before it enters a diagnostic or log."""
    name: object = None
    with contextlib.suppress(Exception):
        name = type_name(error)
    return name if isinstance(name, str) and _CLASS_RE.fullmatch(name) else "Exception"


def _http_status(error: BaseException) -> int | None:
    """Read status metadata from known HTTP adapters, never their messages."""
    status: object = None
    with contextlib.suppress(Exception):
        if isinstance(error, (httpx.HTTPStatusError, httpx2.HTTPStatusError)):
            status = error.response.status_code
        elif isinstance(error, (anthropic.APIStatusError, FeedFetchError)):
            status = error.status_code
        elif isinstance(error, SpotifyException):
            status = error.http_status
        if isinstance(status, int) and 100 <= status <= 599:
            return int(status)
    return None


def _selected_failure(
    error: BaseException,
    default_reason: FailureReason,
) -> tuple[BaseException, int | None, FailureReason]:
    """Prefer the first explicit reason, retaining metadata from that node only."""
    protocol: tuple[BaseException, int | None, FailureReason] | None = None
    for linked in exception_tree(error) or [error]:
        reason = _metadata_attribute(linked, "failure_reason")
        status = _http_status(linked)
        if isinstance(reason, FailureReason):
            return linked, status, reason
        if protocol is None and (is_database_error(linked) or status is not None):
            protocol = linked, status, default_reason
    return protocol or (error, None, default_reason)


def describe_failure(
    source: FailureSource,
    error: BaseException | None = None,
    *,
    reason: FailureReason = FailureReason.REQUEST_FAILED,
) -> str:
    """Render one exception's bounded class, protocol code and closed reason.

    Args:
        source: Code-owned source or stage.
        error: Optional failure, whose message is never read.
        reason: Default code-owned reason when no exception supplies one.

    Returns:
        A bounded diagnostic. Failed metadata reads never escape this boundary.
    """
    result = "discovery: unclassified_failure"
    with contextlib.suppress(Exception):
        if error is None:
            return persisted_failure(f"{source}: {reason}")
        selected, status, reason = _selected_failure(error, reason)
        details = _safe_type_name(selected)
        if is_database_error(selected):
            database = describe_database_error(selected)
            if _DATABASE_RE.fullmatch(database):
                details = database
        elif status is not None:
            details += f" (HTTP {status})"
        result = persisted_failure(f"{source}: {details}; {reason}")
    return result


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


def _safe_exception_identity(error: BaseException) -> bool:
    """Validate names actually printed by a stand-in or a database traceback."""
    with contextlib.suppress(Exception):
        kind = type(error)
        name = _metadata_attribute(kind, "__name__")
        module = _metadata_attribute(kind, "__module__")
        qualname = _metadata_attribute(kind, "__qualname__")
        return (
            isinstance(name, str)
            and _CLASS_RE.fullmatch(name) is not None
            and isinstance(module, str)
            and str.__len__(module) <= MAX_NAMESPACE_LENGTH
            and _NAMESPACE_RE.fullmatch(module) is not None
            and isinstance(qualname, str)
            and str.__len__(qualname) <= MAX_NAMESPACE_LENGTH
            and _QUALNAME_RE.fullmatch(qualname) is not None
        )
    return False


def _safe_database_tree(error: BaseException) -> bool:
    """Identify trees whose printed identities and driver descriptors are bounded."""
    with contextlib.suppress(Exception):
        tree = exception_tree(error)
        return (
            bool(tree)
            and is_database_error(error)
            and all(
                is_database_error(link)
                and _safe_exception_identity(link)
                and _DATABASE_RE.fullmatch(describe_database_error(link))
                for link in tree
            )
        )
    return False


def _context_value(value: str | int) -> str | int:
    """Keep bounded identifier/slug shapes without invoking arbitrary repr methods."""
    with contextlib.suppress(Exception):
        if isinstance(value, int):
            text = int.__repr__(value)
            if str.__len__(text) <= MAX_CONTEXT_LENGTH and _CONTEXT_RE.fullmatch(text):
                return int(text)
            value = text
        if isinstance(value, str) and _CONTEXT_RE.fullmatch(value):
            return str.__getitem__(value, slice(0, MAX_CONTEXT_LENGTH))
    return INVALID_CONTEXT


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
        context: Up to three operation/identifier/page values, validated as
            identifier or slug shapes and truncated to 64 characters each.
    """
    descriptor = persisted_failure(descriptor)
    shown = error
    if not _safe_database_tree(error):
        shown = (
            stand_in(error, descriptor)
            if _safe_exception_identity(error)
            else Exception(descriptor)
        )
        with contextlib.suppress(Exception):
            shown.__traceback__ = error.__traceback__
    if context:
        context = tuple(_context_value(value) for value in context[:MAX_CONTEXT_ITEMS])
        logger.log(level, "%s %s", context, descriptor, exc_info=shown)
    else:
        logger.log(level, "%s", descriptor, exc_info=shown)
