"""Tests for account saves, follows, preferences, and link delivery."""

from email.message import EmailMessage

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import Settings
from podex.models import AccountUser
from podex.services.account_follows import (
    follow_podcast,
    list_followed_podcasts,
    unfollow_podcast,
)
from podex.services.account_preferences import (
    get_account_preferences,
    update_account_preferences,
)
from podex.services.account_saves import (
    list_saved_media,
    remove_saved_media,
    save_media,
)
from podex.services.magic_link_delivery import (
    SmtpMagicLinkSender,
    build_magic_link_sender,
)
from tests.conftest import seed_catalog_graph


def _create_user(db_session: Session) -> AccountUser:
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    return user


def test_save_media_is_idempotent_and_listable(db_session: Session) -> None:
    """Saving twice keeps one row; listing returns the catalog record."""
    graph = seed_catalog_graph(db_session)
    user = _create_user(db_session)

    first = save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    second = save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    db_session.commit()

    assert_that(first).is_not_none()
    assert_that(second).is_not_none()
    listed = list_saved_media(db=db_session, user_id=user.id)
    assert_that(listed).is_length(1)
    assert_that(listed[0].media.title).is_equal_to("Dune")
    assert_that(
        save_media(db=db_session, user_id=user.id, media_id=999_999),
    ).is_none()


def test_remove_saved_media(db_session: Session) -> None:
    """Removing a save deletes the association exactly once."""
    graph = seed_catalog_graph(db_session)
    user = _create_user(db_session)
    save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    db_session.commit()

    assert_that(
        remove_saved_media(db=db_session, user_id=user.id, media_id=graph.media_id),
    ).is_true()
    db_session.commit()
    assert_that(list_saved_media(db=db_session, user_id=user.id)).is_empty()
    assert_that(
        remove_saved_media(db=db_session, user_id=user.id, media_id=graph.media_id),
    ).is_false()


def test_follow_and_unfollow_podcast(db_session: Session) -> None:
    """Following is idempotent and unfollowing removes the association."""
    graph = seed_catalog_graph(db_session)
    user = _create_user(db_session)

    first = follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    second = follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    db_session.commit()

    assert_that(first).is_not_none()
    assert_that(second).is_not_none()
    followed = list_followed_podcasts(db=db_session, user_id=user.id)
    assert_that(followed).is_length(1)
    assert_that(followed[0].podcast.slug).is_equal_to("example-show")
    assert_that(
        follow_podcast(db=db_session, user_id=user.id, podcast_id=999_999),
    ).is_none()
    assert_that(
        unfollow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id),
    ).is_true()
    db_session.commit()
    assert_that(list_followed_podcasts(db=db_session, user_id=user.id)).is_empty()
    assert_that(
        unfollow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id),
    ).is_false()


def test_account_preferences_default_and_update(db_session: Session) -> None:
    """Preferences are created on first read and persist updates."""
    user = _create_user(db_session)

    preferences = get_account_preferences(db=db_session, user_id=user.id)
    db_session.commit()
    assert_that(preferences.digest_enabled).is_true()
    assert_that(preferences.digest_frequency).is_equal_to("daily")

    updated = update_account_preferences(
        db=db_session,
        user_id=user.id,
        digest_enabled=False,
        digest_frequency="weekly",
    )
    db_session.commit()
    assert_that(updated.id).is_equal_to(preferences.id)
    assert_that(updated.digest_enabled).is_false()
    assert_that(updated.digest_frequency).is_equal_to("weekly")


def test_build_magic_link_sender_requires_smtp_configuration() -> None:
    """No sender is built until SMTP host and from-address are configured."""
    assert_that(build_magic_link_sender(settings=Settings())).is_none()
    sender = build_magic_link_sender(
        settings=Settings(
            smtp_host="smtp.example.com",
            smtp_from_email="signin@example.com",
        ),
    )
    assert_that(sender).is_instance_of(SmtpMagicLinkSender)


def test_smtp_sender_builds_single_use_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SMTP adapter logs in when configured and sends one message."""
    sent: dict[str, object] = {}

    class _FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            sent["endpoint"] = (host, port)

        def __enter__(self) -> "_FakeSmtp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def starttls(self) -> None:
            sent["starttls"] = True

        def login(self, username: str, password: str) -> None:
            sent["login"] = username

        def send_message(self, message: EmailMessage) -> None:
            sent["message"] = message

    monkeypatch.setattr("smtplib.SMTP", _FakeSmtp)
    sender = SmtpMagicLinkSender(
        host="smtp.example.com",
        port=587,
        from_email="signin@example.com",
        username="mailer",
        password="secret",  # noqa: S106 # nosec B106 - test fixture credential
        starttls=True,
    )

    sender.send_magic_link(
        email="reader@example.com",
        verification_url="https://podex.example/auth/verify?token=abc",
    )

    message = sent["message"]
    assert_that(sent["endpoint"]).is_equal_to(("smtp.example.com", 587))
    assert_that(sent["starttls"]).is_true()
    assert_that(sent["login"]).is_equal_to("mailer")
    if not isinstance(message, EmailMessage):  # pragma: no cover - narrowed
        raise AssertionError
    assert_that(message["To"]).is_equal_to("reader@example.com")
    assert_that(message.get_content()).contains("https://podex.example/auth/verify")
