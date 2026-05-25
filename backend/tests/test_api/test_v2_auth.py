"""Tests for passwordless v2 account authentication endpoints."""

from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

from assertpy import assert_that
from fastapi.testclient import TestClient
from httpx import Response
from pytest import MonkeyPatch
from sqlalchemy.orm import Session

from podex.api.v2 import auth as v2_auth_api
from podex.config import Settings
from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountDigest,
    AccountFollowedPodcast,
    AccountPreference,
    AccountQuotaUsage,
    AccountSavedMedia,
    AccountSubscription,
    AccountUser,
    Episode,
    MagicLinkToken,
    Media,
    Mention,
    Podcast,
    UserSession,
)
from podex.services.billing_checkout import BillingCheckout


class CapturingMagicLinkSender:
    """Capture delivered sign-in links for deterministic API tests."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str]] = []

    def send_magic_link(self, *, email: str, verification_url: str) -> None:
        """Store an outgoing sign-in message."""
        self.deliveries.append((email, verification_url))


class CapturingDigestSender:
    """Capture delivered notification digests for deterministic API tests."""

    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str, str]] = []

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Store an outgoing digest message."""
        self.deliveries.append((email, subject, body_text))


class CapturingBillingProvider:
    """Capture a hosted checkout request made through the provider boundary."""

    def create_checkout(
        self,
        *,
        email: str,
        account_reference: str,
    ) -> BillingCheckout:
        """Return a deterministic external upgrade destination."""
        return BillingCheckout(
            provider="billing-test",
            checkout_url=f"https://billing.example/upgrade?ref={account_reference}&email={email}",
        )


def _extract_token(sender: CapturingMagicLinkSender, index: int = -1) -> str:
    """Extract the raw sign-in token from a captured link."""
    return parse_qs(urlparse(sender.deliveries[index][1]).query)["token"][0]


def _extract_session_cookie(response: Response) -> str:
    """Read the opaque session credential from a TestClient response."""
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["podex_session"].value


def test_magic_link_session_lifecycle_uses_hashed_credentials(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify sign-in delivery, session use, and logout revoke hashed tokens."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )

    requested = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": " Reader@Example.com ", "redirect_path": "/account/saved"},
    )

    assert_that(requested.status_code).is_equal_to(202)
    assert_that(requested.headers["x-ratelimit-bucket"]).is_equal_to("auth")
    assert_that(sender.deliveries[0][0]).is_equal_to("reader@example.com")
    assert_that(sender.deliveries[0][1]).contains("redirect_path=%2Faccount%2Fsaved")
    token = _extract_token(sender)
    challenge = db_session.query(MagicLinkToken).one()
    assert_that(challenge.token_digest).is_not_equal_to(token)

    verified = client.post("/api/v2/auth/magic-link/verify", json={"token": token})

    assert_that(verified.status_code).is_equal_to(200)
    assert_that(verified.json()["user"]["id"]).is_equal_to("usr_1")
    assert_that(verified.json()["user"]["email"]).is_equal_to("reader@example.com")
    assert_that(verified.headers["set-cookie"]).contains("HttpOnly", "Secure")
    session_token = _extract_session_cookie(verified)
    session = db_session.query(UserSession).one()
    assert_that(session.token_digest).is_not_equal_to(session_token)

    current = client.get(
        "/api/v2/me",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    signed_out = client.post(
        "/api/v2/auth/logout",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    after_logout = client.get(
        "/api/v2/me",
        headers={"Cookie": f"podex_session={session_token}"},
    )

    assert_that(current.status_code).is_equal_to(200)
    assert_that(signed_out.json()["signed_out"]).is_true()
    assert_that(after_logout.status_code).is_equal_to(401)


def test_magic_link_is_single_use_and_superseded_by_new_request(
    client: TestClient,
) -> None:
    """Verify old and already-consumed links cannot create sessions."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    for _index in range(2):
        response = client.post(
            "/api/v2/auth/magic-link/request",
            json={"email": "reader@example.com"},
        )
        assert_that(response.status_code).is_equal_to(202)

    superseded = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender, index=0)},
    )
    accepted = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender, index=1)},
    )
    replay = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender, index=1)},
    )

    assert_that(superseded.status_code).is_equal_to(401)
    assert_that(accepted.status_code).is_equal_to(200)
    assert_that(replay.status_code).is_equal_to(401)


def test_magic_link_rejects_external_redirects_and_requires_delivery_config(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify unsafe continuations and unavailable email do not create accounts."""
    external_redirect = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com", "redirect_path": "https://bad.example"},
    )
    no_delivery = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )

    assert_that(external_redirect.status_code).is_equal_to(422)
    assert_that(no_delivery.status_code).is_equal_to(503)
    assert_that(db_session.query(AccountUser).count()).is_zero()


def test_authenticated_user_can_manage_saved_public_media(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify save, list, idempotency, and delete for public media records."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    db_session.add(
        Media(
            type="book",
            title="A Saved Reference",
            author="An Author",
            description="Public catalog content",
        ),
    )
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    first = client.put("/api/v2/me/saves/med_1", headers=cookie)
    duplicate = client.put("/api/v2/me/saves/med_1", headers=cookie)
    listed = client.get("/api/v2/me/saves", headers=cookie)
    removed = client.delete("/api/v2/me/saves/med_1", headers=cookie)
    removed_again = client.delete("/api/v2/me/saves/med_1", headers=cookie)

    assert_that(first.status_code).is_equal_to(200)
    assert_that(first.json()["media"]["title"]).is_equal_to("A Saved Reference")
    assert_that(duplicate.status_code).is_equal_to(200)
    assert_that(listed.json()["total"]).is_equal_to(1)
    assert_that(listed.json()["items"][0]["media"]["id"]).is_equal_to("med_1")
    assert_that(db_session.query(AccountSavedMedia).count()).is_equal_to(0)
    assert_that(removed.json()["deleted"]).is_true()
    assert_that(removed_again.json()["deleted"]).is_false()


def test_saved_media_requires_authentication_and_public_media_target(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify saved-media operations cannot target absent or unauthenticated media."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    unauthenticated = client.get("/api/v2/me/saves")
    missing = client.put("/api/v2/me/saves/med_404", headers=cookie)
    malformed = client.put("/api/v2/me/saves/pod_1", headers=cookie)

    assert_that(unauthenticated.status_code).is_equal_to(401)
    assert_that(missing.status_code).is_equal_to(404)
    assert_that(malformed.status_code).is_equal_to(404)
    assert_that(db_session.query(AccountSavedMedia).count()).is_zero()


def test_authenticated_user_can_manage_followed_public_podcasts(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify follow, list, idempotency, and unfollow for public sources."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    db_session.add(
        Podcast(
            name="A Followed Source",
            slug="a-followed-source",
            description="A public source",
            status="active",
        ),
    )
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    first = client.put("/api/v2/me/follows/pod_1", headers=cookie)
    duplicate = client.put("/api/v2/me/follows/pod_1", headers=cookie)
    listed = client.get("/api/v2/me/follows", headers=cookie)
    removed = client.delete("/api/v2/me/follows/pod_1", headers=cookie)
    removed_again = client.delete("/api/v2/me/follows/pod_1", headers=cookie)

    assert_that(first.status_code).is_equal_to(200)
    assert_that(first.json()["podcast"]["slug"]).is_equal_to("a-followed-source")
    assert_that(duplicate.status_code).is_equal_to(200)
    assert_that(listed.json()["total"]).is_equal_to(1)
    assert_that(listed.json()["items"][0]["podcast"]["id"]).is_equal_to("pod_1")
    assert_that(removed.json()["deleted"]).is_true()
    assert_that(removed_again.json()["deleted"]).is_false()
    assert_that(db_session.query(AccountFollowedPodcast).count()).is_zero()


def test_followed_podcasts_require_authentication_and_public_target(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify follows reject unauthenticated and absent public sources."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    unauthenticated = client.get("/api/v2/me/follows")
    missing = client.put("/api/v2/me/follows/pod_404", headers=cookie)
    malformed = client.put("/api/v2/me/follows/med_1", headers=cookie)

    assert_that(unauthenticated.status_code).is_equal_to(401)
    assert_that(missing.status_code).is_equal_to(404)
    assert_that(malformed.status_code).is_equal_to(404)
    assert_that(db_session.query(AccountFollowedPodcast).count()).is_zero()


def test_alert_rules_generate_events_only_for_new_public_activity(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify eligible alert rules emit events when published counts advance."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    podcast = Podcast(name="Alert Source", slug="alert-source", status="active")
    media = Media(type="book", title="Alert Book", author="Author")
    db_session.add_all([podcast, media])
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Existing Episode")
    db_session.add(episode)
    db_session.flush()
    db_session.add(Mention(episode_id=episode.id, media_id=media.id))
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}
    client.put("/api/v2/me/saves/med_1", headers=cookie)
    client.put("/api/v2/me/follows/pod_1", headers=cookie)

    media_rule = client.post(
        "/api/v2/me/alerts",
        headers=cookie,
        json={
            "target_type": "media",
            "target_id": "med_1",
            "event_type": "new_mention",
        },
    )
    podcast_rule = client.post(
        "/api/v2/me/alerts",
        headers=cookie,
        json={
            "target_type": "podcast",
            "target_id": "pod_1",
            "event_type": "new_episode",
        },
    )
    db_session.add(Episode(podcast_id=podcast.id, title="New Episode"))
    db_session.flush()
    db_session.add(Mention(episode_id=episode.id, media_id=media.id))
    db_session.commit()

    generated = client.post("/api/v2/me/alerts/evaluate", headers=cookie)
    repeated = client.post("/api/v2/me/alerts/evaluate", headers=cookie)
    paused = client.patch(
        f"/api/v2/me/alerts/{media_rule.json()['id']}",
        headers=cookie,
        json={"enabled": False},
    )
    deleted = client.delete(
        f"/api/v2/me/alerts/{podcast_rule.json()['id']}",
        headers=cookie,
    )

    assert_that(media_rule.json()["baseline_count"]).is_equal_to(1)
    assert_that(podcast_rule.json()["baseline_count"]).is_equal_to(1)
    assert_that(generated.json()["generated"]).is_equal_to(2)
    assert_that(repeated.json()["generated"]).is_zero()
    assert_that(paused.json()["enabled"]).is_false()
    assert_that(deleted.json()["deleted"]).is_true()
    assert_that(db_session.query(AccountAlertEvent).count()).is_equal_to(2)
    assert_that(db_session.query(AccountAlertRule).count()).is_equal_to(1)


def test_alert_rules_require_account_linked_compatible_targets(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify alert creation rejects resources not saved or followed."""
    sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        sender
    )
    db_session.add(Media(type="book", title="Unsaved Book"))
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    not_saved = client.post(
        "/api/v2/me/alerts",
        headers=cookie,
        json={
            "target_type": "media",
            "target_id": "med_1",
            "event_type": "new_mention",
        },
    )
    client.put("/api/v2/me/saves/med_1", headers=cookie)
    incompatible = client.post(
        "/api/v2/me/alerts",
        headers=cookie,
        json={
            "target_type": "media",
            "target_id": "med_1",
            "event_type": "new_episode",
        },
    )

    assert_that(not_saved.status_code).is_equal_to(400)
    assert_that(incompatible.status_code).is_equal_to(400)
    assert_that(db_session.query(AccountAlertRule).count()).is_zero()


def test_pending_alert_activity_is_delivered_in_a_digest_once(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify pending alert events become one delivered digest and are consumed."""
    link_sender = CapturingMagicLinkSender()
    digest_sender = CapturingDigestSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        link_sender
    )
    client.app.dependency_overrides[v2_auth_api.get_digest_sender] = lambda: (
        digest_sender
    )
    podcast = Podcast(name="Digest Source", slug="digest-source", status="active")
    media = Media(type="book", title="Digest Book")
    db_session.add_all([podcast, media])
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Digest Episode")
    db_session.add(episode)
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(link_sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}
    client.put("/api/v2/me/saves/med_1", headers=cookie)
    client.post(
        "/api/v2/me/alerts",
        headers=cookie,
        json={
            "target_type": "media",
            "target_id": "med_1",
            "event_type": "new_mention",
        },
    )
    db_session.add(Mention(episode_id=episode.id, media_id=media.id))
    db_session.commit()

    delivered = client.post("/api/v2/me/digests/send", headers=cookie)
    repeated = client.post("/api/v2/me/digests/send", headers=cookie)
    listed = client.get("/api/v2/me/digests", headers=cookie)

    assert_that(delivered.status_code).is_equal_to(200)
    assert_that(delivered.json()["delivered"]).is_true()
    assert_that(delivered.json()["digest"]["id"]).is_equal_to("mail_1")
    assert_that(digest_sender.deliveries[0][0]).is_equal_to("reader@example.com")
    assert_that(digest_sender.deliveries[0][2]).contains("1 new mention of Digest Book")
    assert_that(repeated.json()["delivered"]).is_false()
    assert_that(listed.json()["total"]).is_equal_to(1)
    assert_that(db_session.query(AccountDigest).count()).is_equal_to(1)
    assert_that(db_session.query(AccountAlertEvent).one().digest_id).is_equal_to(1)


def test_digest_delivery_requires_email_configuration(
    client: TestClient,
) -> None:
    """Verify digest sending fails clearly when SMTP delivery is unavailable."""
    link_sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        link_sender
    )
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(link_sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    response = client.post("/api/v2/me/digests/send", headers=cookie)

    assert_that(response.status_code).is_equal_to(503)


def test_account_preferences_control_digest_delivery(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify disabled notifications suppress sends without SMTP configuration."""
    link_sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        link_sender
    )
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(link_sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    defaults = client.get("/api/v2/me/preferences", headers=cookie)
    updated = client.patch(
        "/api/v2/me/preferences",
        headers=cookie,
        json={"digest_enabled": False, "digest_frequency": "weekly"},
    )
    suppressed = client.post("/api/v2/me/digests/send", headers=cookie)

    assert_that(defaults.json()["digest_enabled"]).is_true()
    assert_that(defaults.json()["digest_frequency"]).is_equal_to("daily")
    assert_that(updated.json()["digest_enabled"]).is_false()
    assert_that(updated.json()["digest_frequency"]).is_equal_to("weekly")
    assert_that(suppressed.status_code).is_equal_to(200)
    assert_that(suppressed.json()["delivered"]).is_false()
    preference = db_session.query(AccountPreference).one()
    assert_that(preference.digest_frequency).is_equal_to("weekly")


def test_subscription_status_starts_free_and_checkout_is_launch_gated(
    client: TestClient,
) -> None:
    """Verify free subscription defaults do not enable unreviewed paid checkout."""
    link_sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        link_sender
    )
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(link_sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    subscription = client.get("/api/v2/me/subscription", headers=cookie)
    checkout = client.post("/api/v2/me/subscription/checkout", headers=cookie)

    assert_that(subscription.json()["tier"]).is_equal_to("free")
    assert_that(subscription.json()["paid_access"]).is_false()
    assert_that(subscription.json()["paid_tier_enabled"]).is_false()
    assert_that(subscription.json()["quotas"][0]["feature"]).is_equal_to("api_requests")
    assert_that(subscription.json()["quotas"][0]["used"]).is_zero()
    assert_that(subscription.json()["quotas"][1]["feature"]).is_equal_to("llm_requests")
    assert_that(checkout.status_code).is_equal_to(503)


def test_paid_feature_enforcement_meters_actions_and_supports_checkout(
    client: TestClient,
    db_session: Session,
    monkeypatch: MonkeyPatch,
) -> None:
    """Verify active paid users are metered while free users are rejected."""
    settings = Settings(
        paid_tier_enabled=True,
        paid_tier_enforced=True,
        paid_api_requests_per_month=1,
        paid_llm_requests_per_month=2,
    )
    monkeypatch.setattr(v2_auth_api, "get_settings", lambda: settings)
    link_sender = CapturingMagicLinkSender()
    client.app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: (
        link_sender
    )
    client.app.dependency_overrides[v2_auth_api.get_billing_provider] = lambda: (
        CapturingBillingProvider()
    )
    db_session.add_all(
        [
            Media(type="book", title="Paid Book"),
            Media(type="movie", title="Quota Movie"),
        ],
    )
    db_session.commit()
    client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    verified = client.post(
        "/api/v2/auth/magic-link/verify",
        json={"token": _extract_token(link_sender)},
    )
    cookie = {"Cookie": f"podex_session={_extract_session_cookie(verified)}"}

    free_attempt = client.put("/api/v2/me/saves/med_1", headers=cookie)
    user = db_session.query(AccountUser).one()
    db_session.add(AccountSubscription(user_id=user.id, tier="paid", status="active"))
    db_session.commit()
    paid_attempt = client.put("/api/v2/me/saves/med_1", headers=cookie)
    over_limit = client.put("/api/v2/me/saves/med_2", headers=cookie)
    subscription = client.get("/api/v2/me/subscription", headers=cookie)
    checkout = client.post("/api/v2/me/subscription/checkout", headers=cookie)

    assert_that(free_attempt.status_code).is_equal_to(402)
    assert_that(paid_attempt.status_code).is_equal_to(200)
    assert_that(over_limit.status_code).is_equal_to(429)
    assert_that(subscription.json()["paid_access"]).is_true()
    assert_that(subscription.json()["quotas"][0]["used"]).is_equal_to(1)
    assert_that(subscription.json()["quotas"][0]["remaining"]).is_zero()
    assert_that(subscription.json()["quotas"][1]["remaining"]).is_equal_to(2)
    assert_that(checkout.json()["provider"]).is_equal_to("billing-test")
    assert_that(checkout.json()["checkout_url"]).contains("usr_1")
    assert_that(db_session.query(AccountQuotaUsage).one().units).is_equal_to(1)
