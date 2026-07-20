"""Tests for the Paddle checkout provider and signed webhook route."""

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import BillingSettings, Settings
from podex.models import AccountSubscription, AccountUser, BillingWebhookEvent
from podex.services.billing_checkout import (
    HostedBillingCheckoutProvider,
    PaddleBillingCheckoutProvider,
    build_billing_checkout_provider,
)
from podex.services.billing_webhooks import (
    PaddleSignatureFormatError,
    PaddleSignatureVerificationError,
    verify_paddle_signature,
)

_SECRET = "whsec_test_secret"  # nosec B105 - test-only signing secret
_WEBHOOK_PATH = "/api/v2/billing/webhooks/paddle"


def _sign(body: bytes, *, secret: str = _SECRET, ts: int | None = None) -> str:
    """Build a valid ``ts=...;h1=...`` Paddle signature header for a body."""
    timestamp = int(time.time()) if ts is None else ts
    digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=f"{timestamp}:".encode() + body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"ts={timestamp};h1={digest}"


def _configure_paddle(client: TestClient) -> None:
    """Point the app's settings dependency at a Paddle-enabled config."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        billing=BillingSettings(paddle_webhook_secret=_SECRET),
    )


def _create_user(db_session: Session) -> AccountUser:
    """Persist one account user for entitlement assertions."""
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.commit()
    return user


def _subscription_event(
    *,
    user_id: int,
    event_id: str = "evt_001",
    event_type: str = "subscription.activated",
    paddle_status: str = "active",
) -> dict[str, Any]:
    """Build a minimal Paddle subscription event payload."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "data": {
            "id": "sub_123",
            "customer_id": "ctm_456",
            "status": paddle_status,
            "custom_data": {"account_reference": str(user_id)},
            "current_billing_period": {
                "starts_at": "2026-07-20T00:00:00+00:00",
                "ends_at": "2026-08-20T00:00:00+00:00",
            },
        },
    }


def _post_event(client: TestClient, payload: dict[str, Any]) -> Any:
    """Sign and deliver one webhook payload to the Paddle route."""
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        _WEBHOOK_PATH,
        content=body,
        headers={"Paddle-Signature": _sign(body)},
    )


def test_paddle_provider_builds_prefilled_checkout() -> None:
    """The Paddle bridge carries email and an opaque account reference."""
    provider = build_billing_checkout_provider(
        settings=Settings(
            billing=BillingSettings(
                paddle_checkout_url="https://buy.paddle.example/checkout",
                paddle_price_id="pri_123",
            ),
        ),
    )
    assert_that(provider).is_instance_of(PaddleBillingCheckoutProvider)
    if provider is None:  # pragma: no cover - narrowed above
        raise AssertionError

    checkout = provider.create_checkout(
        email="reader@example.com",
        account_reference="7",
    )

    assert_that(checkout.provider).is_equal_to("paddle")
    assert_that(checkout.checkout_url).contains(
        "price_id=pri_123",
        "prefilled_email=reader%40example.com",
        "account_reference=7",
    )
    assert_that(checkout.checkout_url).does_not_contain(
        "account_reference=reader",
    )


def test_provider_builder_prefers_paddle_then_hosted() -> None:
    """Paddle settings win; the hosted bridge and None fallback remain."""
    assert_that(build_billing_checkout_provider(settings=Settings())).is_none()
    hosted = build_billing_checkout_provider(
        settings=Settings(
            billing=BillingSettings(
                provider_name="hosted-test",
                checkout_url="https://billing.example/upgrade",
            ),
        ),
    )
    assert_that(hosted).is_instance_of(HostedBillingCheckoutProvider)
    paddle = build_billing_checkout_provider(
        settings=Settings(
            billing=BillingSettings(
                provider_name="hosted-test",
                checkout_url="https://billing.example/upgrade",
                paddle_checkout_url="https://buy.paddle.example/checkout",
                paddle_price_id="pri_123",
            ),
        ),
    )
    assert_that(paddle).is_instance_of(PaddleBillingCheckoutProvider)


def test_verify_signature_accepts_valid_header() -> None:
    """A freshly signed body passes verification."""
    body = b'{"event_id": "evt"}'
    verify_paddle_signature(
        signature_header=_sign(body),
        raw_body=body,
        secret=_SECRET,
    )


@pytest.mark.parametrize(
    "header",
    ["", "ts=abc;h1=deadbeef", "h1=deadbeef", "ts=123", "nonsense"],
    ids=["empty", "non_int_ts", "missing_ts", "missing_h1", "no_pairs"],
)
def test_verify_signature_rejects_malformed_header(header: str) -> None:
    """Headers without a parseable ts/h1 pair raise a format error."""
    with pytest.raises(PaddleSignatureFormatError):
        verify_paddle_signature(
            signature_header=header,
            raw_body=b"{}",
            secret=_SECRET,
        )


def test_verify_signature_rejects_bad_digest_and_stale_ts() -> None:
    """Wrong digests and out-of-tolerance timestamps fail verification."""
    body = b'{"event_id": "evt"}'
    bad_sig = _sign(body, secret="wrong-secret")  # nosec B106 - wrong on purpose
    with pytest.raises(PaddleSignatureVerificationError):
        verify_paddle_signature(
            signature_header=bad_sig,
            raw_body=body,
            secret=_SECRET,
        )
    with pytest.raises(PaddleSignatureVerificationError):
        verify_paddle_signature(
            signature_header=_sign(body, ts=int(time.time()) - 3600),
            raw_body=body,
            secret=_SECRET,
        )


def test_webhook_route_unconfigured_returns_503(client: TestClient) -> None:
    """Without a webhook secret the route refuses deliveries."""
    response = client.post(_WEBHOOK_PATH, content=b"{}")

    assert_that(response.status_code).is_equal_to(503)


def test_webhook_route_rejects_missing_and_bad_signatures(
    client: TestClient,
) -> None:
    """Missing headers, bad digests, and stale timestamps never process."""
    _configure_paddle(client)
    body = b'{"event_id": "evt_sig", "event_type": "subscription.activated"}'

    missing = client.post(_WEBHOOK_PATH, content=body)
    assert_that(missing.status_code).is_equal_to(401)

    bad_sig = _sign(body, secret="wrong-secret")  # nosec B106 - wrong on purpose
    bad_digest = client.post(
        _WEBHOOK_PATH,
        content=body,
        headers={"Paddle-Signature": bad_sig},
    )
    assert_that(bad_digest.status_code).is_equal_to(401)

    stale = client.post(
        _WEBHOOK_PATH,
        content=body,
        headers={"Paddle-Signature": _sign(body, ts=int(time.time()) - 3600)},
    )
    assert_that(stale.status_code).is_equal_to(401)

    malformed = client.post(
        _WEBHOOK_PATH,
        content=body,
        headers={"Paddle-Signature": "not-a-signature"},
    )
    assert_that(malformed.status_code).is_equal_to(400)


def test_webhook_route_rejects_invalid_payloads(client: TestClient) -> None:
    """Signed but unusable bodies are rejected with 400."""
    _configure_paddle(client)
    for body in (b"not json", b'{"event_type": "x"}', b'{"event_id": "evt"}'):
        response = client.post(
            _WEBHOOK_PATH,
            content=body,
            headers={"Paddle-Signature": _sign(body)},
        )
        assert_that(response.status_code).is_equal_to(400)


def test_webhook_activates_subscription(
    client: TestClient,
    db_session: Session,
) -> None:
    """An activation event upgrades the entitlement with provider ids."""
    _configure_paddle(client)
    user = _create_user(db_session)

    response = _post_event(client, _subscription_event(user_id=user.id))

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["status"]).is_equal_to("processed")
    subscription = db_session.query(AccountSubscription).one()
    assert_that(subscription.tier).is_equal_to("paid")
    assert_that(subscription.status).is_equal_to("active")
    assert_that(subscription.billing_provider).is_equal_to("paddle")
    assert_that(subscription.provider_customer_id).is_equal_to("ctm_456")
    assert_that(subscription.provider_subscription_id).is_equal_to("sub_123")
    assert_that(subscription.current_period_ends_at).is_not_none()


def test_webhook_replay_is_acked_without_reprocessing(
    client: TestClient,
    db_session: Session,
) -> None:
    """A replayed event id is acknowledged but applied only once."""
    _configure_paddle(client)
    user = _create_user(db_session)
    payload = _subscription_event(user_id=user.id, event_id="evt_replay")

    first = _post_event(client, payload)
    assert_that(first.status_code).is_equal_to(200)
    subscription = db_session.query(AccountSubscription).one()
    subscription.tier = "free"
    subscription.status = "canceled"
    db_session.commit()

    replay = _post_event(client, payload)

    assert_that(replay.status_code).is_equal_to(200)
    assert_that(replay.json()["status"]).is_equal_to("duplicate")
    db_session.expire_all()
    subscription = db_session.query(AccountSubscription).one()
    assert_that(subscription.tier).is_equal_to("free")
    assert_that(subscription.status).is_equal_to("canceled")
    assert_that(db_session.query(BillingWebhookEvent).count()).is_equal_to(1)


def test_webhook_cancellation_revokes_access(
    client: TestClient,
    db_session: Session,
) -> None:
    """A cancellation event ends paid access."""
    _configure_paddle(client)
    user = _create_user(db_session)
    _post_event(client, _subscription_event(user_id=user.id))

    response = _post_event(
        client,
        _subscription_event(
            user_id=user.id,
            event_id="evt_cancel",
            event_type="subscription.canceled",
            paddle_status="canceled",
        ),
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["status"]).is_equal_to("processed")
    db_session.expire_all()
    subscription = db_session.query(AccountSubscription).one()
    assert_that(subscription.tier).is_equal_to("free")
    assert_that(subscription.status).is_equal_to("canceled")


def test_webhook_unknown_event_and_unknown_account_are_acked(
    client: TestClient,
    db_session: Session,
) -> None:
    """Non-subscription events and unmatched references ack as ignored."""
    _configure_paddle(client)

    unknown_type = _post_event(
        client,
        {
            "event_id": "evt_txn",
            "event_type": "transaction.completed",
            "data": {"id": "txn_1"},
        },
    )
    assert_that(unknown_type.status_code).is_equal_to(200)
    assert_that(unknown_type.json()["status"]).is_equal_to("ignored")

    unknown_account = _post_event(
        client,
        _subscription_event(user_id=999_999, event_id="evt_ghost"),
    )
    assert_that(unknown_account.status_code).is_equal_to(200)
    assert_that(unknown_account.json()["status"]).is_equal_to("ignored")
    assert_that(db_session.query(AccountSubscription).count()).is_equal_to(0)
    assert_that(db_session.query(BillingWebhookEvent).count()).is_equal_to(2)
