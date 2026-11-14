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
- Request data: method, URL and headers of the failing request. Query
  strings are dropped by :func:`scrub_event` on every route (the OAuth
  callback carries ``code`` and ``state`` there); cookies and credential
  headers are filtered by the SDK's event scrubber; ``send_default_pii`` is
  off, so no client IP or user identity is attached.
- Breadcrumbs: log records (after the application's logging filters) and
  outbound HTTP requests. The HTTP breadcrumb's ``http.query`` keeps the raw
  query, which carries provider API keys, so it is in the scrubber denylist.
"""

import re

import sentry_sdk
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber
from sentry_sdk.types import Event, Hint

from podex import __version__
from podex.config import Settings

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REDACTED = "[redacted-email]"

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


def _drop_query(request: dict[str, object]) -> None:
    """Remove the query string and fragment from captured request data.

    Args:
        request: The event's ``request`` mapping, edited in place.
    """
    request.pop("query_string", None)
    url = request.get("url")
    if isinstance(url, str):
        request["url"] = url.split("#", 1)[0].split("?", 1)[0]


def scrub_event(event: Event, hint: Hint) -> Event:
    """``before_send`` hook that strips PII and query strings from an event.

    Scrubs email addresses from the event message and ``extra`` values, drops
    ``user.email`` entirely (scrubbing the remaining user fields), and removes
    the request query string, which can carry OAuth codes or other secrets.

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
        _drop_query(request)
    return event


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
        before_send=scrub_event,
        send_default_pii=False,
        include_local_variables=False,
        event_scrubber=EventScrubber(
            denylist=[*DEFAULT_DENYLIST, *_EXTRA_DENYLIST],
            recursive=True,
        ),
    )
    return True
