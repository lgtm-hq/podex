"""Paddle webhook signature verification and idempotent entitlement updates.

This module (together with the checkout provider) is the only place that
knows Paddle's wire formats: the ``Paddle-Signature`` header scheme and the
subscription event payload shape. Everything it writes goes through the
provider-neutral ``AccountSubscription`` model.
"""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from podex.models import AccountUser, BillingWebhookEvent
from podex.services.account_subscriptions import get_account_subscription
from podex.services.billing_checkout import (
    ACCOUNT_REFERENCE_KEY,
    PADDLE_PROVIDER_NAME,
)

PADDLE_SIGNATURE_HEADER = "Paddle-Signature"
"""Header carrying Paddle's ``ts=...;h1=...`` webhook signature."""

SIGNATURE_TOLERANCE_SECONDS = 300
"""Maximum accepted skew between the signed timestamp and receipt time."""

_ACTIVE_PADDLE_STATUSES = frozenset({"active", "trialing"})
_SUBSCRIPTION_EVENT_PREFIX = "subscription."


class PaddleSignatureFormatError(Exception):
    """Raised when the signature header cannot be parsed at all."""


class PaddleSignatureVerificationError(Exception):
    """Raised when a parsed signature is stale or fails the HMAC check."""


def verify_paddle_signature(
    *,
    signature_header: str,
    raw_body: bytes,
    secret: str,
    now: datetime | None = None,
) -> None:
    """Verify a Paddle ``ts``/``h1`` HMAC-SHA256 webhook signature.

    Args:
        signature_header: Raw ``Paddle-Signature`` header value.
        raw_body: Exact request body bytes the signature covers.
        secret: Shared webhook signing secret from settings.
        now: Injection point for the current time in tests.

    Raises:
        PaddleSignatureFormatError: When the header is not ``ts=...;h1=...``.
        PaddleSignatureVerificationError: When the timestamp is stale or no
            provided ``h1`` digest matches the computed one.
    """
    timestamp: int | None = None
    digests: list[str] = []
    for element in signature_header.split(";"):
        key, separator, value = element.strip().partition("=")
        if not separator:
            continue
        if key == "ts":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise PaddleSignatureFormatError from exc
        elif key == "h1":
            digests.append(value)
    if timestamp is None or not digests:
        raise PaddleSignatureFormatError
    received_at = (now or datetime.now(UTC)).timestamp()
    if abs(received_at - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        raise PaddleSignatureVerificationError
    expected = hmac.new(
        key=secret.encode("utf-8"),
        msg=f"{timestamp}:".encode() + raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected, digest) for digest in digests):
        raise PaddleSignatureVerificationError


def record_billing_webhook_event(
    *,
    db: Session,
    provider: str,
    event_id: str,
    event_type: str,
) -> bool:
    """Record a webhook event id, reporting whether it is new.

    Args:
        db: Database session shared with the request.
        provider: Billing provider label (for example ``paddle``).
        event_id: Provider-unique event identifier.
        event_type: Provider event type, stored for observability.

    Returns:
        ``True`` when the event was recorded now, ``False`` on replay.
    """
    already_processed = (
        db.query(BillingWebhookEvent)
        .filter(
            BillingWebhookEvent.provider == provider,
            BillingWebhookEvent.event_id == event_id,
        )
        .first()
    )
    if already_processed is not None:
        return False
    db.add(
        BillingWebhookEvent(
            provider=provider,
            event_id=event_id,
            event_type=event_type,
        ),
    )
    db.flush()
    return True


def _parse_account_id(data: dict[str, Any]) -> int | None:
    """Extract the opaque account reference threaded through checkout."""
    custom_data = data.get("custom_data")
    if not isinstance(custom_data, dict):
        return None
    reference = custom_data.get(ACCOUNT_REFERENCE_KEY)
    try:
        return int(str(reference))
    except (TypeError, ValueError):
        return None


def _parse_period_end(data: dict[str, Any]) -> datetime | None:
    """Extract the current billing period end, if the payload carries one."""
    period = data.get("current_billing_period")
    if not isinstance(period, dict):
        return None
    ends_at = period.get("ends_at")
    if not isinstance(ends_at, str):
        return None
    try:
        return datetime.fromisoformat(ends_at)
    except ValueError:
        return None


def apply_paddle_subscription_event(
    *,
    db: Session,
    event_type: str,
    data: dict[str, Any],
) -> bool:
    """Apply one Paddle subscription lifecycle event to an entitlement.

    Unknown event types, missing/unparseable account references, and
    references that match no account are acked without effect.

    Args:
        db: Database session shared with the request.
        event_type: Paddle event type (for example ``subscription.activated``).
        data: The event's ``data`` object.

    Returns:
        ``True`` when an ``AccountSubscription`` was updated.
    """
    if not event_type.startswith(_SUBSCRIPTION_EVENT_PREFIX):
        return False
    user_id = _parse_account_id(data)
    if user_id is None:
        return False
    user_exists = db.query(AccountUser.id).filter(AccountUser.id == user_id).first()
    if user_exists is None:
        return False
    subscription = get_account_subscription(db=db, user_id=user_id)
    paddle_status = str(data.get("status") or "")
    is_canceled = event_type == "subscription.canceled"
    is_active = not is_canceled and paddle_status in _ACTIVE_PADDLE_STATUSES
    subscription.billing_provider = PADDLE_PROVIDER_NAME
    customer_id = data.get("customer_id")
    if isinstance(customer_id, str) and customer_id:
        subscription.provider_customer_id = customer_id
    subscription_id = data.get("id")
    if isinstance(subscription_id, str) and subscription_id:
        subscription.provider_subscription_id = subscription_id
    subscription.current_period_ends_at = _parse_period_end(data)
    if is_active:
        subscription.tier = "paid"
        subscription.status = "active"
    elif is_canceled:
        subscription.tier = "free"
        subscription.status = "canceled"
    else:
        subscription.tier = "free"
        subscription.status = paddle_status or "inactive"
    db.flush()
    return True
