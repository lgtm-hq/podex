"""Signed billing webhook intake for the Paddle provider.

The route verifies Paddle's ``ts``/``h1`` HMAC signature over the exact raw
body before touching the payload, stores processed event ids so replays are
acknowledged without reprocessing, and applies subscription lifecycle events
to the provider-neutral ``AccountSubscription`` entitlement.
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from podex.api.deps import AppSettings, DbSession
from podex.schemas.billing import BillingWebhookAck
from podex.services.billing_checkout import PADDLE_PROVIDER_NAME
from podex.services.billing_webhooks import (
    PADDLE_SIGNATURE_HEADER,
    PaddleSignatureFormatError,
    PaddleSignatureVerificationError,
    apply_paddle_subscription_event,
    record_billing_webhook_event,
    verify_paddle_signature,
)

router = APIRouter(tags=["billing"])

_ACK_PROCESSED = "processed"
_ACK_DUPLICATE = "duplicate"
_ACK_IGNORED = "ignored"


def _parse_event(raw_body: bytes) -> tuple[str, str, dict[str, Any]]:
    """Decode a verified webhook body into event id, type, and data."""
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload is not valid JSON",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be a JSON object",
        )
    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    if not isinstance(event_id, str) or not event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload is missing event_id",
        )
    if not isinstance(event_type, str) or not event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload is missing event_type",
        )
    data = payload.get("data")
    return event_id, event_type, data if isinstance(data, dict) else {}


async def receive_paddle_webhook(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> BillingWebhookAck:
    """Verify, dedupe, and apply one signed Paddle webhook delivery."""
    if not settings.paddle_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paddle webhooks are not configured",
        )
    signature_header = request.headers.get(PADDLE_SIGNATURE_HEADER, "")
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Paddle signature",
        )
    raw_body = await request.body()
    try:
        verify_paddle_signature(
            signature_header=signature_header,
            raw_body=raw_body,
            secret=settings.paddle_webhook_secret,
        )
    except PaddleSignatureFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed Paddle signature",
        ) from exc
    except PaddleSignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Paddle signature",
        ) from exc
    event_id, event_type, data = _parse_event(raw_body)
    if not record_billing_webhook_event(
        db=db,
        provider=PADDLE_PROVIDER_NAME,
        event_id=event_id,
        event_type=event_type,
    ):
        return BillingWebhookAck(status=_ACK_DUPLICATE)
    applied = apply_paddle_subscription_event(
        db=db,
        event_type=event_type,
        data=data,
    )
    db.commit()
    return BillingWebhookAck(status=_ACK_PROCESSED if applied else _ACK_IGNORED)


router.add_api_route(
    "/billing/webhooks/paddle",
    receive_paddle_webhook,
    methods=["POST"],
    response_model=BillingWebhookAck,
)
