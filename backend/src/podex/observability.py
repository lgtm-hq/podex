"""Sentry error tracking, disabled unless a DSN is configured.

``init_sentry`` is called from both deployables — the FastAPI app factory
(``podex.main.create_app``) and the scheduler runner process
(``podex.scheduler_runner.main``) — so each process reports independently.
Events pass through :func:`scrub_event` to strip email addresses and request
query strings before leaving the process.

What the SDK sends, and how credentials are kept out of it:

- Stack frames: source context only. Local-variable capture is off, because
  frame locals hold OAuth codes, state values, settings with API keys and
  outbound requests whose bodies carry client secrets.
- Request data: method and URL (without query string or fragment) of the
  failing request, plus only the headers in :data:`REPORTED_REQUEST_HEADERS`.
  Every other header is dropped, so credential headers (``X-Ops-Key``,
  ``Paddle-Signature``, ``Authorization``, ``Cookie``, and any added later)
  never leave the process. Request bodies, cookies and the server
  environment are not sent; ``send_default_pii`` is off, so no client IP or
  user identity is attached.
- Breadcrumbs: log records (after the application's logging filters) and
  outbound HTTP requests. The access logger is excluded, because its records
  carry the client address. The HTTP breadcrumb's ``http.query`` keeps the
  raw query, which carries provider API keys, so it is in the scrubber
  denylist.
- Free text and data (messages, exception values and source context,
  breadcrumb messages and data, extra, non-SDK contexts, request and user
  data): URL userinfo and the known secret values of the loaded settings are
  replaced by :mod:`podex.redaction` in ``before_send`` and
  ``before_breadcrumb``. Structural fields (identifiers, enums, timestamps,
  release and SDK metadata, mapping keys) are never rewritten, so the event
  stays valid even when a short secret matches them. Breadcrumb data keeps
  only the allowlisted headers at any depth, as request data does. A
  database error's message (statement, parameters, driver ``DETAIL`` with
  row values) is replaced by its class and SQLSTATE
  (:mod:`podex.database_errors`), in exception groups and chains alike.
- The provider's rejection of a bad sign-in code, and the callback's handled
  ``502`` for it, are not sent: they are caller-triggerable, already logged,
  and reporting them would let any caller spend the reporting quota. They
  are recognised by two dedicated exception types, each constructible in
  exactly one place. Any other error is sent, even if the rejection is in
  its exception chain.
"""

import contextlib
import logging
import re
from typing import Any, cast

import sentry_sdk
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from podex import __version__
from podex.api.v2.auth_rejection import RejectedSignInCodeError
from podex.config import Settings
from podex.database_errors import (
    WITHHELD,
    describe_database_error,
    exception_class_names,
    exception_tree,
    is_database_error,
    is_database_stand_in,
    placeholder,
    safe_str,
)
from podex.redaction import (
    SHORT_VALUE_LENGTH,
    SecretRedactor,
    redact_in_place,
    secret_values,
    undersized_field_paths,
)
from podex.services.workos_auth import WorkOSInvalidCodeError

_logger = logging.getLogger(__name__)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REDACTED = "[redacted-email]"
_ACCESS_LOGGER = "podex.access"

REPORTED_REQUEST_HEADERS = frozenset(
    {"content-length", "content-type", "host", "user-agent"},
)
"""Request headers kept in error reports; every other header is dropped.

The request id is not taken from the raw ``X-Request-ID`` header: the
request-context middleware validates it and sets the ``request_id`` tag.
"""


# Replaces URL userinfo and the secret values of the settings the reporter was
# last initialised with; until then, URL userinfo only.
_REDACTOR = SecretRedactor()


# Free-text and data-bearing parts of an event, redacted for URL userinfo and
# configured secret values. Everything else is never rewritten, because its
# values are schema-constrained or come from code and the runtime rather than
# from application data: identifiers (event_id, trace and span ids), enums
# (level, platform), timestamps, release/environment/dist, sdk and module
# metadata, exception types/modules/mechanisms, frame locations (filename,
# function, module, line), the SDK-generated contexts below, breadcrumb
# type/category/level/timestamp, and every mapping key.
_REDACTED_TEXT_FIELDS = ("message", "transaction", "culprit", "server_name", "logger")
_REDACTED_DATA_FIELDS = ("logentry", "extra", "request", "user", "tags", "fingerprint")
# Modules of database errors whose reported value is replaced even when the
# exception itself is not in the hint (for example a message-only event).
_DATABASE_MODULES = ("psycopg", "sqlite3", "sqlalchemy.exc")
# Event fields copied into the placeholder event sent when minimising fails.
_PLACEHOLDER_EVENT_FIELDS = (
    "event_id",
    "timestamp",
    "level",
    "platform",
    "release",
    "environment",
    "dist",
    "sdk",
)
_REDACTED_FRAME_FIELDS = ("context_line", "pre_context", "post_context", "vars")
_STRUCTURAL_CONTEXTS = frozenset(
    {"trace", "runtime", "os", "device", "app", "cloud_resource", "otel"},
)


# Keys the SDK's scrubber filters in addition to its defaults, wherever they
# appear (request bodies and headers, extras, breadcrumb data), recursively.
_EXTRA_DENYLIST = (
    # Outbound-request breadcrumbs keep the raw query and fragment.
    "http.query",
    "http.fragment",
    # OAuth parameters.
    "code",
    "state",
    "client_secret",
)


def _scrub_text(value: str) -> str:
    """Replace any email addresses in ``value`` with a redaction marker.

    Args:
        value: Text that may contain email addresses.

    Returns:
        The text with every email address replaced by ``[redacted-email]``.
    """
    return _EMAIL_RE.sub(_REDACTED, value)


def _scrub_mapping(data: dict[str, object]) -> dict[str, object]:
    """Scrub email addresses from every string value in a mapping.

    Args:
        data: Mapping whose string values may contain email addresses.

    Returns:
        A new mapping with email addresses redacted from string values.
    """
    return {
        key: _scrub_text(value) if isinstance(value, str) else value
        for key, value in data.items()
    }


def _allowlisted_headers(headers: dict[Any, Any]) -> dict[Any, Any]:
    """Return only the reported headers of a header mapping.

    Args:
        headers: Header names and values.

    Returns:
        dict: The entries whose name is in :data:`REPORTED_REQUEST_HEADERS`.
    """
    return {
        name: value
        for name, value in headers.items()
        if isinstance(name, str) and name.lower() in REPORTED_REQUEST_HEADERS
    }


def _allowlist_headers_recursively(data: dict[Any, Any] | list[Any]) -> None:
    """Reduce every nested ``headers`` mapping to the allowlisted headers.

    Args:
        data: Breadcrumb data, edited in place at any depth.
    """
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict | list):
                _allowlist_headers_recursively(item)
        return
    for key, value in data.items():
        if isinstance(key, str) and key.lower() == "headers":
            data[key] = _allowlisted_headers(value) if isinstance(value, dict) else {}
        elif isinstance(value, dict | list):
            _allowlist_headers_recursively(value)


def _redact_frames(container: dict[Any, Any]) -> None:
    """Redact the source context and variables of a stacktrace's frames."""
    stacktrace = container.get("stacktrace")
    frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
    for frame in frames if isinstance(frames, list) else ():
        if not isinstance(frame, dict):
            continue
        for field in _REDACTED_FRAME_FIELDS:
            value = frame.get(field)
            if isinstance(value, str):
                frame[field] = _REDACTOR.redact(value)
            elif isinstance(value, dict | list):
                redact_in_place(value, _REDACTOR.redact)


def _redact_event(event: dict[str, Any]) -> None:
    """Redact the free-text and data-bearing fields of an event.

    Breadcrumbs are not touched here: each was redacted once by
    :func:`before_breadcrumb` when recorded, and redacting twice would
    rewrite the markers a short secret's redaction inserted.

    Args:
        event: The event, edited in place.
    """
    redact = _REDACTOR.redact
    for field in _REDACTED_TEXT_FIELDS:
        text = event.get(field)
        if isinstance(text, str):
            event[field] = redact(text)
    for field in _REDACTED_DATA_FIELDS:
        data = event.get(field)
        if isinstance(data, dict | list):
            redact_in_place(data, redact)
    contexts = event.get("contexts")
    if isinstance(contexts, dict):
        for name, context in contexts.items():
            if name not in _STRUCTURAL_CONTEXTS and isinstance(context, dict | list):
                redact_in_place(context, redact)
    for field in ("exception", "threads"):
        container = event.get(field)
        values = container.get("values") if isinstance(container, dict) else None
        for value in values if isinstance(values, list) else ():
            if not isinstance(value, dict):
                continue
            if isinstance(value.get("value"), str):
                value["value"] = redact(value["value"])
            _redact_frames(value)


def _minimise_request(request: dict[str, object]) -> None:
    """Reduce captured request data to what is safe and useful to report.

    Drops the query string, fragment, body, cookies and server environment,
    and keeps only the allowlisted headers.

    Args:
        request: The event's ``request`` mapping, edited in place.
    """
    for key in ("query_string", "data", "cookies", "env"):
        request.pop(key, None)
    url = request.get("url")
    if isinstance(url, str):
        request["url"] = url.split("#", 1)[0].split("?", 1)[0]
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = _allowlisted_headers(headers)
    else:
        request.pop("headers", None)


def _is_unreported(hint: Hint) -> bool:
    """Return whether the reported exception is the expected bad-code rejection.

    Only two exception types, each constructible in exactly one place, are
    dropped: :class:`WorkOSInvalidCodeError` (raised only by the WorkOS
    exchange client for the provider's ``invalid_grant`` answer) and
    :class:`RejectedSignInCodeError` (the 502 raised only by the sign-in
    callback, explicitly from that rejection). Neither can be subclassed, so
    ``isinstance`` matches exactly those types. Anything else is reported,
    including any other exception whose cause or context holds the
    rejection: a real server error raised while handling it must not be
    hidden, and a caller cannot create a dropped type by sending a bad code.

    Args:
        hint: The SDK hint, carrying ``exc_info`` for exception events.

    Returns:
        bool: True when the event is for the expected rejection only.
    """
    exc_info = hint.get("exc_info")
    if not isinstance(exc_info, tuple) or not isinstance(exc_info[1], BaseException):
        return False
    error = exc_info[1]
    if isinstance(error, WorkOSInvalidCodeError):
        return True
    return isinstance(error, RejectedSignInCodeError) and isinstance(
        error.__cause__,
        WorkOSInvalidCodeError,
    )


def scrub_event(event: Event, hint: Hint) -> Event:
    """Minimise an event's data before it is sent.

    Replaces URL userinfo and known secret values in the free-text and
    data-bearing fields (messages, exception values and source context,
    extra, non-SDK contexts, request and user data), leaving structural
    fields valid, scrubs email
    addresses from the event message and ``extra`` values, drops
    ``user.email`` entirely (scrubbing the remaining user fields), and
    reduces request data to the method, the URL without query string, and
    the allowlisted headers.

    Args:
        event: Sentry event payload about to be sent.
        hint: Additional context from the SDK (unused).

    Returns:
        The scrubbed event.
    """
    del hint
    _redact_event(cast(dict[str, Any], event))
    message = event.get("message")
    if isinstance(message, str):
        event["message"] = _scrub_text(message)
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = _scrub_mapping(extra)
    user = event.get("user")
    if isinstance(user, dict):
        user.pop("email", None)
        event["user"] = _scrub_mapping(user)
    request = event.get("request")
    if isinstance(request, dict):
        _minimise_request(request)
    return event


def _database_errors(hint: Hint) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """Return the hint's database errors as (text, descriptor), keyed by class.

    Each class's errors are listed in the order the tree is walked, so each
    reported value can be matched to its own error. Stand-ins the log
    boundary printed in place of database errors count too.
    """
    exc_info = hint.get("exc_info")
    if not isinstance(exc_info, tuple) or not isinstance(exc_info[1], BaseException):
        return {}
    found: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for link in exception_tree(exc_info[1]):
        key = (type(link).__module__, type(link).__qualname__)
        if is_database_error(link):
            found.setdefault(key, []).append(
                (safe_str(link), describe_database_error(link)),
            )
        elif is_database_stand_in(link):
            # Already sanitised by the log boundary before reaching the
            # reporter (its process exception hook): keep its descriptor.
            text = safe_str(link)
            found.setdefault(key, []).append(
                (text, text.removesuffix(f" {WITHHELD}")),
            )
    return found


def _take_descriptor(candidates: list[tuple[str, str]], reported: object) -> str | None:
    """Remove and return the descriptor of the error a reported value came from.

    The value is the error's text, possibly truncated by the SDK: an exact or
    prefix match wins; otherwise the next unmatched error of that class.
    """
    if isinstance(reported, str):
        prefix = reported.removesuffix("...")
        for index, (text, _descriptor) in enumerate(candidates):
            if text == reported or (prefix and text.startswith(prefix)):
                return candidates.pop(index)[1]
    return candidates.pop(0)[1] if candidates else None


def _withhold_database_errors(event: Event, hint: Hint) -> None:
    """Replace the message of each reported database error by its descriptor.

    Exception values are matched to the hint's database errors by class and
    then by their own text, so two errors of one class keep their own
    SQLSTATE. A value from a database module with no matching error gets a
    class-only descriptor; its original text is never kept.

    Args:
        event: The event, edited in place.
        hint: SDK context; ``exc_info`` holds the reported exception.
    """
    candidates = _database_errors(hint)
    exception = event.get("exception")
    values = exception.get("values") if isinstance(exception, dict) else None
    for value in values if isinstance(values, list) else ():
        if not isinstance(value, dict):
            continue
        module, kind = value.get("module"), value.get("type")
        if not isinstance(module, str) or not isinstance(kind, str):
            continue
        pool = candidates.get((module, kind))
        if pool is None and not module.startswith(_DATABASE_MODULES):
            continue
        descriptor = _take_descriptor(pool or [], value.get("value"))
        value["value"] = f"{descriptor or kind} {WITHHELD}"


def _placeholder_event(event: Event, hint: Hint) -> Event:
    """Return the event sent when minimising failed: structure and a placeholder.

    Only schema-constrained fields are kept; the message names the
    exception classes, so the failure is still visible.
    """
    exc_info = hint.get("exc_info")
    error = exc_info[1] if isinstance(exc_info, tuple) else None
    classes = exception_class_names(error) if isinstance(error, BaseException) else []
    original = cast(dict[str, Any], event)
    kept: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        kept = {
            field: original[field]
            for field in _PLACEHOLDER_EVENT_FIELDS
            if field in original
        }
    kept["message"] = placeholder(event, classes=classes)
    return cast(Event, kept)


def before_send(event: Event, hint: Hint) -> Event | None:
    """``before_send`` hook: drop unreported errors, minimise the rest.

    Args:
        event: Sentry event payload about to be sent.
        hint: SDK context; ``exc_info`` identifies the reported exception.

    Returns:
        The minimised event, or ``None`` to drop it.
    """
    if _is_unreported(hint):
        return None
    sent: Event | None = None
    with contextlib.suppress(Exception):
        _withhold_database_errors(event, hint)
        sent = scrub_event(event, hint)
    return sent if sent is not None else _placeholder_event(event, hint)


def before_breadcrumb(crumb: Breadcrumb, hint: BreadcrumbHint) -> Breadcrumb:
    """``before_breadcrumb`` hook: keep credentials out of breadcrumbs.

    Header mappings anywhere in breadcrumb data keep only the allowlisted
    headers, and URL userinfo and known secret values are replaced in the
    message and in data values. Type, category, level, timestamp and keys
    are structural and kept as they are.

    Args:
        crumb: The breadcrumb about to be recorded.
        hint: SDK context (unused).

    Returns:
        The minimised breadcrumb.
    """
    del hint
    minimised = False
    with contextlib.suppress(Exception):
        data = crumb.get("data")
        if isinstance(data, dict):
            _allowlist_headers_recursively(data)
            redact_in_place(data, _REDACTOR.redact)
        message = crumb.get("message")
        if isinstance(message, str):
            crumb["message"] = _REDACTOR.redact(message)
        minimised = True
    if minimised:
        return crumb
    category = crumb.get("category")
    return {
        "category": category if isinstance(category, str) else "default",
        "message": placeholder(crumb),
    }


def init_sentry(settings: Settings) -> bool:
    """Initialize the Sentry SDK when a DSN is configured.

    No-ops (leaving the SDK fully disabled) when
    ``settings.observability.sentry_dsn`` is empty. Tracing and profiling stay
    disabled — error tracking only.

    Args:
        settings: Runtime settings providing the DSN and environment.

    Returns:
        True when the SDK was initialized, False when disabled.
    """
    if not settings.observability.sentry_dsn:
        return False
    _REDACTOR.configure(secret_values(settings))
    # Field paths only, computed from field names: nothing derived from a
    # configured value is logged.
    for field_path in undersized_field_paths(settings):
        _logger.warning(
            "setting %s is shorter than %d characters; error reports "
            "redact every occurrence of its value",
            field_path,
            SHORT_VALUE_LENGTH,
        )
    sentry_sdk.init(
        dsn=settings.observability.sentry_dsn,
        environment=settings.observability.sentry_environment,
        release=f"podex@{__version__}",
        before_send=before_send,
        before_breadcrumb=before_breadcrumb,
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        event_scrubber=EventScrubber(
            denylist=[*DEFAULT_DENYLIST, *_EXTRA_DENYLIST],
            recursive=True,
        ),
    )
    # Access records carry the client address; keep them out of breadcrumbs.
    ignore_logger(_ACCESS_LOGGER)
    return True
