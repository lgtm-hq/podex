"""Sentry error tracking, disabled unless a DSN is configured.

``init_sentry`` is called from both deployables — the FastAPI app factory
(``podex.main.create_app``) and the scheduler runner process
(``podex.scheduler_runner.main``) — so each process reports independently.
Events pass through :func:`scrub_event` to strip email addresses before
leaving the process.
"""

import re

import sentry_sdk
from sentry_sdk.types import Event, Hint

from podex import __version__
from podex.config import Settings

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_REDACTED = "[redacted-email]"


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


def scrub_event(event: Event, hint: Hint) -> Event:
    """``before_send`` hook that strips PII (emails) from an event.

    Scrubs email addresses from the event message and ``extra`` values, and
    drops ``user.email`` entirely (scrubbing the remaining user fields).

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
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialize the Sentry SDK when a DSN is configured.

    No-ops (leaving the SDK fully disabled) when ``settings.sentry_dsn`` is
    empty. Tracing and profiling stay disabled — error tracking only.

    Args:
        settings: Runtime settings providing the DSN and environment.

    Returns:
        True when the SDK was initialized, False when disabled.
    """
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=f"podex@{__version__}",
        before_send=scrub_event,
        send_default_pii=False,
    )
    return True
