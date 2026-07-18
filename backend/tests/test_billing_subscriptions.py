"""Tests for subscription entitlement, quotas, and checkout endpoints."""

from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import AccountSubscription, AccountUser
from podex.services.account_subscriptions import (
    API_REQUESTS,
    AccountQuotaExceededError,
    PaidSubscriptionRequiredError,
    consume_paid_api_request,
    get_account_subscription,
    get_quota_snapshot,
    has_paid_access,
)
from podex.services.billing_checkout import (
    BillingCheckout,
    HostedBillingCheckoutProvider,
    build_billing_checkout_provider,
)
from tests.test_api_auth import _sign_in

_NOW = datetime(2026, 7, 19, tzinfo=UTC)


def _create_user(db_session: Session) -> AccountUser:
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def test_subscription_defaults_to_free(db_session: Session) -> None:
    """First entitlement lookup creates an active free subscription."""
    user = _create_user(db_session)

    subscription = get_account_subscription(db=db_session, user_id=user.id)
    db_session.commit()

    assert_that(subscription.tier).is_equal_to("free")
    assert_that(subscription.status).is_equal_to("active")
    assert_that(has_paid_access(subscription=subscription)).is_false()
    again = get_account_subscription(db=db_session, user_id=user.id)
    assert_that(again.id).is_equal_to(subscription.id)


def test_consume_paid_api_request_enforcement(db_session: Session) -> None:
    """Enforcement requires paid entitlement and honors monthly limits."""
    user = _create_user(db_session)
    relaxed = Settings(paid_tier_enforced=False)
    assert_that(
        consume_paid_api_request(db=db_session, user_id=user.id, settings=relaxed),
    ).is_none()

    enforced = Settings(paid_tier_enforced=True, paid_api_requests_per_month=2)
    with pytest.raises(PaidSubscriptionRequiredError):
        consume_paid_api_request(db=db_session, user_id=user.id, settings=enforced)

    subscription = get_account_subscription(db=db_session, user_id=user.id)
    subscription.tier = "paid"
    db_session.flush()

    first = consume_paid_api_request(
        db=db_session,
        user_id=user.id,
        settings=enforced,
        now=_NOW,
    )
    second = consume_paid_api_request(
        db=db_session,
        user_id=user.id,
        settings=enforced,
        now=_NOW,
    )
    db_session.commit()

    assert_that(first).is_not_none()
    if first is None or second is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(first.used).is_equal_to(1)
    assert_that(second.used).is_equal_to(2)
    assert_that(second.remaining).is_equal_to(0)
    with pytest.raises(AccountQuotaExceededError):
        consume_paid_api_request(
            db=db_session,
            user_id=user.id,
            settings=enforced,
            now=_NOW,
        )
    snapshot = get_quota_snapshot(
        db=db_session,
        user_id=user.id,
        settings=enforced,
        feature=API_REQUESTS,
        now=_NOW,
    )
    assert_that(snapshot.used).is_equal_to(2)


def test_checkout_provider_builds_prefilled_url() -> None:
    """The hosted bridge appends non-secret context to the checkout URL."""
    assert_that(build_billing_checkout_provider(settings=Settings())).is_none()
    provider = build_billing_checkout_provider(
        settings=Settings(
            billing_provider_name="hosted-test",
            billing_checkout_url="https://billing.example/upgrade",
        ),
    )
    assert_that(provider).is_instance_of(HostedBillingCheckoutProvider)
    if provider is None:  # pragma: no cover - narrowed above
        raise AssertionError

    checkout = provider.create_checkout(
        email="reader@example.com",
        account_reference="7",
    )

    assert_that(checkout).is_instance_of(BillingCheckout)
    assert_that(checkout.provider).is_equal_to("hosted-test")
    assert_that(checkout.checkout_url).contains(
        "prefilled_email=reader%40example.com",
        "reference=7",
    )


def test_subscription_endpoint_reports_quotas(client: TestClient) -> None:
    """The subscription endpoint returns tier and both feature quotas."""
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}

    response = client.get("/api/v2/me/subscription", headers=cookie)

    assert_that(response.status_code).is_equal_to(200)
    body = response.json()
    assert_that(body["tier"]).is_equal_to("free")
    assert_that(body["paid_features_enforced"]).is_false()
    features = {quota["feature"] for quota in body["quotas"]}
    assert_that(features).is_equal_to({"api_requests", "llm_requests"})


def test_checkout_endpoint_requires_launch_gate(client: TestClient) -> None:
    """Checkout stays unavailable until the paid tier launch gate opens."""
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}

    response = client.post("/api/v2/me/subscription/checkout", headers=cookie)

    assert_that(response.status_code).is_equal_to(503)


def test_paid_enforcement_blocks_personalization_writes(
    client: TestClient,
    db_session: Session,
) -> None:
    """With enforcement on, free accounts get 402 and paid accounts proceed."""
    from tests.conftest import seed_catalog_graph

    graph = seed_catalog_graph(db_session)
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}
    app = client.app
    from fastapi import FastAPI

    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    from podex.api.deps import get_app_settings

    app.dependency_overrides[get_app_settings] = lambda: Settings(
        paid_tier_enforced=True,
    )

    blocked = client.put(f"/api/v2/me/saves/{graph.media_id}", headers=cookie)
    assert_that(blocked.status_code).is_equal_to(402)

    user = db_session.query(AccountUser).one()
    subscription = get_account_subscription(db=db_session, user_id=user.id)
    subscription.tier = "paid"
    db_session.commit()
    assert_that(db_session.query(AccountSubscription).one().tier).is_equal_to("paid")

    allowed = client.put(f"/api/v2/me/saves/{graph.media_id}", headers=cookie)
    assert_that(allowed.status_code).is_equal_to(200)
