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
- Free text (exception messages, log messages, breadcrumb text and data):
  URL userinfo and the known secret values of the loaded settings are
  replaced by :mod:`podex.redaction` in ``before_send`` and
  ``before_breadcrumb``, whatever field holds the text. Breadcrumb data keeps
  only the allowlisted headers, as request data does.
- The provider's rejection of a bad sign-in code, and the callback's handled
  ``502`` for it, are not sent: they are caller-triggerable, already logged,
  and reporting them would let any caller spend the reporting quota. They
  are recognised by two dedicated exception types, each constructible in
  exactly one place. Any other error is sent, even if the rejection is in
  its exception chain.
"""

import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from podex import __version__
from podex.api.v2.auth_rejection import RejectedSignInCodeError
from podex.config import Settings
from podex.redaction import SecretRedactor, redact_in_place, secret_values
from podex.services.workos_auth import WorkOSInvalidCodeError

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

    Replaces URL userinfo and known secret values in every string of the
    event (messages, exception values, breadcrumbs, extras), scrubs email
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
    redact_in_place(event, _REDACTOR.redact)
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
    return scrub_event(event, hint)


def before_breadcrumb(crumb: Breadcrumb, hint: BreadcrumbHint) -> Breadcrumb:
    """``before_breadcrumb`` hook: keep credentials out of breadcrumbs.

    Header mappings in breadcrumb data keep only the allowlisted headers, and
    URL userinfo and known secret values are replaced in every string.

    Args:
        crumb: The breadcrumb about to be recorded.
        hint: SDK context (unused).

    Returns:
        The minimised breadcrumb.
    """
    del hint
    data = crumb.get("data")
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.lower() == "headers":
                data[key] = (
                    _allowlisted_headers(value) if isinstance(value, dict) else {}
                )
    redact_in_place(crumb, _REDACTOR.redact)
    return crumb


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
