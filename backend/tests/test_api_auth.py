"""Tests for passwordless v2 account authentication endpoints."""

from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from podex.api.v2 import auth as v2_auth_api
from podex.models import Episode, MagicLinkToken, UserSession
from tests.conftest import seed_catalog_graph


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


def _sign_in(client: TestClient) -> str:
    """Complete the magic-link flow and return the session cookie value."""
    sender = CapturingMagicLinkSender()
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: sender
    requested = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    if requested.status_code != 202:  # pragma: no cover - guard
        raise AssertionError(requested.text)
    token = parse_qs(urlparse(sender.deliveries[-1][1]).query)["token"][0]
    verified = client.post("/api/v2/auth/magic-link/verify", json={"token": token})
    if verified.status_code != 200:  # pragma: no cover - guard
        raise AssertionError(verified.text)
    return _extract_session_cookie(verified)


def _extract_session_cookie(response: Response) -> str:
    """Read the opaque session credential from a TestClient response."""
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie["podex_session"].value


def test_magic_link_session_lifecycle_uses_hashed_credentials(
    client: TestClient,
    db_session: Session,
) -> None:
    """Sign-in delivery, session use, and logout revoke hashed tokens."""
    sender = CapturingMagicLinkSender()
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[v2_auth_api.get_auth_magic_link_sender] = lambda: sender

    requested = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": " Reader@Example.com ", "redirect_path": "/account/saved"},
    )

    assert_that(requested.status_code).is_equal_to(202)
    assert_that(sender.deliveries[0][0]).is_equal_to("reader@example.com")
    assert_that(sender.deliveries[0][1]).contains("redirect_path=%2Faccount%2Fsaved")
    token = parse_qs(urlparse(sender.deliveries[0][1]).query)["token"][0]
    challenge = db_session.query(MagicLinkToken).one()
    assert_that(challenge.token_digest).is_not_equal_to(token)

    verified = client.post("/api/v2/auth/magic-link/verify", json={"token": token})

    assert_that(verified.status_code).is_equal_to(200)
    assert_that(verified.json()["user"]["email"]).is_equal_to("reader@example.com")
    assert_that(verified.headers["set-cookie"]).contains("HttpOnly", "Secure")
    session_token = _extract_session_cookie(verified)
    session = db_session.query(UserSession).one()
    assert_that(session.token_digest).is_not_equal_to(session_token)

    current = client.get(
        "/api/v2/me",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    assert_that(current.status_code).is_equal_to(200)
    assert_that(current.json()["email"]).is_equal_to("reader@example.com")

    replay = client.post("/api/v2/auth/magic-link/verify", json={"token": token})
    assert_that(replay.status_code).is_equal_to(401)

    logout = client.post(
        "/api/v2/auth/logout",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    assert_that(logout.status_code).is_equal_to(200)
    assert_that(logout.json()["signed_out"]).is_true()
    after = client.get(
        "/api/v2/me",
        headers={"Cookie": f"podex_session={session_token}"},
    )
    assert_that(after.status_code).is_equal_to(401)


def test_magic_link_request_requires_configured_delivery(
    client: TestClient,
) -> None:
    """Without SMTP configuration the sign-in endpoint reports 503."""
    response = client.post(
        "/api/v2/auth/magic-link/request",
        json={"email": "reader@example.com"},
    )
    assert_that(response.status_code).is_equal_to(503)


def test_me_requires_authentication(client: TestClient) -> None:
    """Account endpoints reject anonymous requests."""
    assert_that(client.get("/api/v2/me").status_code).is_equal_to(401)
    assert_that(client.get("/api/v2/me/saves").status_code).is_equal_to(401)
    assert_that(client.get("/api/v2/me/alerts").status_code).is_equal_to(401)


def test_saves_and_follows_round_trip(
    client: TestClient,
    db_session: Session,
) -> None:
    """Saves and follows persist, list, and delete through the API."""
    graph = seed_catalog_graph(db_session)
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}

    saved = client.put(f"/api/v2/me/saves/{graph.media_id}", headers=cookie)
    assert_that(saved.status_code).is_equal_to(200)
    assert_that(saved.json()["media"]["title"]).is_equal_to("Dune")
    missing = client.put("/api/v2/me/saves/999999", headers=cookie)
    assert_that(missing.status_code).is_equal_to(404)
    listed = client.get("/api/v2/me/saves", headers=cookie)
    assert_that(listed.json()["total"]).is_equal_to(1)
    removed = client.delete(f"/api/v2/me/saves/{graph.media_id}", headers=cookie)
    assert_that(removed.json()["deleted"]).is_true()

    followed = client.put(f"/api/v2/me/follows/{graph.podcast_id}", headers=cookie)
    assert_that(followed.status_code).is_equal_to(200)
    assert_that(followed.json()["podcast"]["slug"]).is_equal_to("example-show")
    follows = client.get("/api/v2/me/follows", headers=cookie)
    assert_that(follows.json()["total"]).is_equal_to(1)
    unfollowed = client.delete(
        f"/api/v2/me/follows/{graph.podcast_id}",
        headers=cookie,
    )
    assert_that(unfollowed.json()["deleted"]).is_true()


def test_alert_rule_crud_and_evaluation(
    client: TestClient,
    db_session: Session,
) -> None:
    """Alert rules require linked targets and generate events on growth."""
    graph = seed_catalog_graph(db_session)
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}

    unlinked = client.post(
        "/api/v2/me/alerts",
        json={
            "target_type": "podcast",
            "target_id": graph.podcast_id,
            "event_type": "new_episode",
        },
        headers=cookie,
    )
    assert_that(unlinked.status_code).is_equal_to(400)

    client.put(f"/api/v2/me/follows/{graph.podcast_id}", headers=cookie)
    created = client.post(
        "/api/v2/me/alerts",
        json={
            "target_type": "podcast",
            "target_id": graph.podcast_id,
            "event_type": "new_episode",
        },
        headers=cookie,
    )
    assert_that(created.status_code).is_equal_to(200)
    rule_id = created.json()["id"]

    db_session.add(
        Episode(podcast_id=graph.podcast_id, title="Pilot II", episode_number=2),
    )
    db_session.commit()
    evaluated = client.post("/api/v2/me/alerts/evaluate", headers=cookie)
    assert_that(evaluated.json()["generated"]).is_equal_to(1)

    paused = client.patch(
        f"/api/v2/me/alerts/{rule_id}",
        json={"enabled": False},
        headers=cookie,
    )
    assert_that(paused.json()["enabled"]).is_false()
    assert_that(
        client.patch(
            "/api/v2/me/alerts/999999",
            json={"enabled": True},
            headers=cookie,
        ).status_code,
    ).is_equal_to(404)
    deleted = client.delete(f"/api/v2/me/alerts/{rule_id}", headers=cookie)
    assert_that(deleted.json()["deleted"]).is_true()
    rules = client.get("/api/v2/me/alerts", headers=cookie)
    assert_that(rules.json()["total"]).is_equal_to(0)


def test_digest_send_and_preferences(
    client: TestClient,
    db_session: Session,
) -> None:
    """Digest delivery honors preferences and lists delivered digests."""
    graph = seed_catalog_graph(db_session)
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}
    digest_sender = CapturingDigestSender()
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[v2_auth_api.get_digest_sender] = lambda: digest_sender
    client.put(f"/api/v2/me/follows/{graph.podcast_id}", headers=cookie)
    client.post(
        "/api/v2/me/alerts",
        json={
            "target_type": "podcast",
            "target_id": graph.podcast_id,
            "event_type": "new_episode",
        },
        headers=cookie,
    )
    db_session.add(
        Episode(podcast_id=graph.podcast_id, title="Pilot II", episode_number=2),
    )
    db_session.commit()

    sent = client.post("/api/v2/me/digests/send", headers=cookie)
    assert_that(sent.status_code).is_equal_to(200)
    assert_that(sent.json()["delivered"]).is_true()
    assert_that(digest_sender.deliveries).is_length(1)
    listed = client.get("/api/v2/me/digests", headers=cookie)
    assert_that(listed.json()["total"]).is_equal_to(1)

    prefs = client.get("/api/v2/me/preferences", headers=cookie)
    assert_that(prefs.json()["digest_enabled"]).is_true()
    updated = client.patch(
        "/api/v2/me/preferences",
        json={"digest_enabled": False, "digest_frequency": "weekly"},
        headers=cookie,
    )
    assert_that(updated.json()["digest_frequency"]).is_equal_to("weekly")

    disabled = client.post("/api/v2/me/digests/send", headers=cookie)
    assert_that(disabled.json()["delivered"]).is_false()
