"""Tests for self-serve account export and deletion."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountSavedMedia,
    AccountUser,
    MagicLinkToken,
    UserSession,
)
from podex.services.account_alerts import create_alert_rule, evaluate_alert_rules
from podex.services.account_follows import follow_podcast
from podex.services.account_preferences import get_account_preferences
from podex.services.account_saves import save_media
from tests.conftest import seed_catalog_graph
from tests.test_api_auth import _sign_in


def _populate(client: TestClient, db_session: Session) -> dict[str, str]:
    graph = seed_catalog_graph(db_session)
    cookie = {"Cookie": f"podex_session={_sign_in(client)}"}
    user = db_session.query(AccountUser).one()
    save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    get_account_preferences(db=db_session, user_id=user.id)
    db_session.commit()
    return cookie


def test_export_returns_all_account_data(
    client: TestClient,
    db_session: Session,
) -> None:
    """The export contains identity and every personalization collection."""
    cookie = _populate(client, db_session)

    response = client.get("/api/v2/me/export", headers=cookie)

    assert_that(response.status_code).is_equal_to(200)
    export = response.json()
    assert_that(export["account"]["email"]).is_equal_to("reader@example.com")
    assert_that(export["saved_media"]).is_length(1)
    assert_that(export["followed_podcasts"]).is_length(1)
    assert_that(export["alert_rules"]).is_length(1)
    assert_that(export["preferences"]["digest_frequency"]).is_equal_to("daily")
    assert_that(export["subscription"]).is_none()
    assert_that(client.get("/api/v2/me/export").status_code).is_equal_to(401)


def test_delete_account_removes_every_row_and_signs_out(
    client: TestClient,
    db_session: Session,
) -> None:
    """Deletion clears all account rows, audits, and revokes the session."""
    cookie = _populate(client, db_session)
    graph_user = db_session.query(AccountUser).one()
    evaluate_alert_rules(db=db_session, user_id=graph_user.id)
    db_session.commit()

    response = client.delete("/api/v2/me", headers=cookie)

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["signed_out"]).is_true()
    assert_that(db_session.query(AccountUser).count()).is_equal_to(0)
    assert_that(db_session.query(AccountSavedMedia).count()).is_equal_to(0)
    assert_that(db_session.query(AccountAlertRule).count()).is_equal_to(0)
    assert_that(db_session.query(AccountAlertEvent).count()).is_equal_to(0)
    assert_that(db_session.query(UserSession).count()).is_equal_to(0)
    assert_that(db_session.query(MagicLinkToken).count()).is_equal_to(0)

    after = client.get("/api/v2/me", headers=cookie)
    assert_that(after.status_code).is_equal_to(401)
