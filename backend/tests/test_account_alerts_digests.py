"""Tests for account alert rules, evaluation, and digest delivery."""

from datetime import UTC, datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.config import AuthSettings, Settings
from podex.models import (
    AccountAlertEvent,
    AccountUser,
    Episode,
    Mention,
)
from podex.services.account_alerts import (
    create_alert_rule,
    delete_alert_rule,
    evaluate_alert_rules,
    list_alert_rules,
    set_alert_rule_enabled,
)
from podex.services.account_digests import list_account_digests, send_pending_digest
from podex.services.account_follows import follow_podcast
from podex.services.account_saves import save_media
from podex.services.notification_delivery import (
    SmtpDigestSender,
    build_digest_sender,
)
from tests.conftest import SeededGraph, seed_catalog_graph

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


class RecordingDigestSender:
    """In-memory digest delivery for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send_digest(self, *, email: str, subject: str, body_text: str) -> None:
        """Record one delivered digest."""
        self.sent.append((email, subject, body_text))


def _create_linked_user(db_session: Session, graph: SeededGraph) -> AccountUser:
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    follow_podcast(db=db_session, user_id=user.id, podcast_id=graph.podcast_id)
    return user


def test_create_alert_rule_requires_linked_target(db_session: Session) -> None:
    """Rules exist only for saved media or followed podcasts."""
    graph = seed_catalog_graph(db_session)
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()

    orphan = create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    assert_that(orphan).is_none()

    save_media(db=db_session, user_id=user.id, media_id=graph.media_id)
    rule = create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    duplicate = create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    db_session.commit()

    assert_that(rule).is_not_none()
    if rule is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(rule.baseline_count).is_equal_to(1)
    assert_that(duplicate).is_same_as(rule)
    mismatched = create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_episode",
    )
    assert_that(mismatched).is_none()


def test_evaluate_alert_rules_generates_events_on_growth(
    db_session: Session,
) -> None:
    """Evaluation emits one event per rule whose observed count grew."""
    graph = seed_catalog_graph(db_session)
    user = _create_linked_user(db_session, graph)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="podcast",
        target_id=graph.podcast_id,
        event_type="new_episode",
    )
    db_session.commit()

    quiet = evaluate_alert_rules(db=db_session, user_id=user.id, now=_NOW)
    assert_that(quiet).is_empty()

    episode = Episode(podcast_id=graph.podcast_id, title="Pilot II", episode_number=2)
    db_session.add(episode)
    db_session.flush()
    db_session.add(
        Mention(
            episode_id=episode.id,
            media_id=graph.media_id,
            context="mentioned again",
        ),
    )
    db_session.commit()

    generated = evaluate_alert_rules(db=db_session, user_id=user.id, now=_NOW)
    db_session.commit()

    assert_that(generated).is_length(2)
    repeat = evaluate_alert_rules(db=db_session, user_id=user.id, now=_NOW)
    assert_that(repeat).is_empty()


def test_alert_rule_enable_and_delete(db_session: Session) -> None:
    """Rules can be paused, resumed, and removed by their owner only."""
    graph = seed_catalog_graph(db_session)
    user = _create_linked_user(db_session, graph)
    rule = create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    if rule is None:  # pragma: no cover - narrowed above
        raise AssertionError
    db_session.commit()

    paused = set_alert_rule_enabled(
        db=db_session,
        user_id=user.id,
        rule_id=rule.id,
        enabled=False,
    )
    assert_that(paused).is_not_none()
    if paused is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(paused.enabled).is_false()
    assert_that(
        set_alert_rule_enabled(
            db=db_session,
            user_id=user.id + 1,
            rule_id=rule.id,
            enabled=True,
        ),
    ).is_none()
    assert_that(list_alert_rules(db=db_session, user_id=user.id)).is_length(1)
    assert_that(
        delete_alert_rule(db=db_session, user_id=user.id, rule_id=rule.id),
    ).is_true()
    db_session.commit()
    assert_that(
        delete_alert_rule(db=db_session, user_id=user.id, rule_id=rule.id),
    ).is_false()


def test_send_pending_digest_delivers_once(db_session: Session) -> None:
    """Undigested events deliver as one digest and never redeliver."""
    graph = seed_catalog_graph(db_session)
    user = _create_linked_user(db_session, graph)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="podcast",
        target_id=graph.podcast_id,
        event_type="new_episode",
    )
    db_session.add(
        Episode(podcast_id=graph.podcast_id, title="Pilot II", episode_number=2),
    )
    db_session.commit()
    evaluate_alert_rules(db=db_session, user_id=user.id, now=_NOW)
    db_session.commit()
    sender = RecordingDigestSender()

    empty_before_events = send_pending_digest(
        db=db_session,
        user=user,
        sender=sender,
        now=_NOW,
    )
    db_session.commit()

    assert_that(empty_before_events).is_not_none()
    if empty_before_events is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(sender.sent).is_length(1)
    email, subject, body = sender.sent[0]
    assert_that(email).is_equal_to("reader@example.com")
    assert_that(subject).contains("1 new update")
    assert_that(body).contains("1 new episode from The Example Show")
    assert_that(empty_before_events.delivered_at).is_not_none()

    event = db_session.query(AccountAlertEvent).one()
    assert_that(event.digest_id).is_equal_to(empty_before_events.id)
    assert_that(
        send_pending_digest(db=db_session, user=user, sender=sender, now=_NOW),
    ).is_none()
    assert_that(list_account_digests(db=db_session, user_id=user.id)).is_length(1)


def test_digest_summarizes_media_mentions(db_session: Session) -> None:
    """Media-mention events render the media title in the digest body."""
    graph = seed_catalog_graph(db_session)
    user = _create_linked_user(db_session, graph)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=graph.media_id,
        event_type="new_mention",
    )
    db_session.add(
        Mention(
            episode_id=graph.episode_id,
            media_id=graph.media_id,
            context="mentioned again",
        ),
    )
    db_session.commit()
    evaluate_alert_rules(db=db_session, user_id=user.id, now=_NOW)
    sender = RecordingDigestSender()

    digest = send_pending_digest(db=db_session, user=user, sender=sender, now=_NOW)
    db_session.commit()

    assert_that(digest).is_not_none()
    if digest is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(digest.body_text).contains("1 new mention of Dune")


def test_build_digest_sender_requires_smtp_configuration() -> None:
    """No digest sender is built until SMTP settings are present."""
    assert_that(build_digest_sender(settings=Settings())).is_none()
    sender = build_digest_sender(
        settings=Settings(
            auth=AuthSettings(
                smtp_host="smtp.example.com",
                smtp_from_email="digests@example.com",
            ),
        ),
    )
    assert_that(sender).is_instance_of(SmtpDigestSender)
