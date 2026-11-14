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
- Errors whose class sets ``reportable = False`` are not sent, nor is the
  handled ``502`` raised directly from one: they are caller-triggerable
  rejections, already logged, and reporting them would let any caller spend
  the reporting quota. Any other error is sent, even if such a rejection is
  in its exception chain.
"""

import re

import sentry_sdk
from sentry_sdk.integrations.logging import ignore_logger
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint
from starlette.exceptions import HTTPException

from podex import __version__
from podex.config import Settings

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

_REPORTABLE_ATTRIBUTE = "reportable"
# Status of the HTTPException a handler raises from an expected rejection.
_HANDLED_REJECTION_STATUS = 502


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
        request["headers"] = {
            name: value
            for name, value in headers.items()
            if isinstance(name, str) and name.lower() in REPORTED_REQUEST_HEADERS
        }
    else:
        request.pop("headers", None)


def _is_expected_rejection(error: BaseException) -> bool:
    """Return whether ``error``'s class opts out with ``reportable = False``."""
    return getattr(error, _REPORTABLE_ATTRIBUTE, True) is False


def _is_unreported(hint: Hint) -> bool:
    """Return whether the reported exception is an expected rejection.

    Only two shapes are dropped: the rejection itself, and the handled
    ``502`` ``HTTPException`` raised *from* it (``raise ... from error``), as
    the sign-in callback does. Anything else is reported, even when a
    rejection sits in its ``__cause__``/``__context__`` chain: an error
    raised while handling a rejection (a failing logger, a database error,
    a bug) is a real server error, and a caller must not be able to hide it
    by first sending a bad code.

    Args:
        hint: The SDK hint, carrying ``exc_info`` for exception events.

    Returns:
        bool: True when the event is for an expected rejection only.
    """
    exc_info = hint.get("exc_info")
    if not isinstance(exc_info, tuple) or not isinstance(exc_info[1], BaseException):
        return False
    error = exc_info[1]
    if _is_expected_rejection(error):
        return True
    cause = error.__cause__
    return (
        isinstance(error, HTTPException)
        and error.status_code == _HANDLED_REJECTION_STATUS
        and cause is not None
        and _is_expected_rejection(cause)
    )


def scrub_event(event: Event, hint: Hint) -> Event:
    """Minimise an event's data before it is sent.

    Scrubs email addresses from the event message and ``extra`` values, drops
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
    sentry_sdk.init(
        dsn=settings.observability.sentry_dsn,
        environment=settings.observability.sentry_environment,
        release=f"podex@{__version__}",
        before_send=before_send,
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
