"""Authenticated account alert rules and deterministic evaluation.

Writers of media alert rules follow the media-reference lock order (ADR
0020): the media record first (``lock_media``), then rules in ``(user_id,
id)`` order, then their events. Evaluation takes only one user's rules, in id
order, and no media lock.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountFollowedPodcast,
    AccountSavedMedia,
    Episode,
    Mention,
)
from podex.services.content_suppression import (
    count_published_mentions,
    published_mentions,
)
from podex.services.media_locks import lock_media

AlertTargetType = Literal["media", "podcast"]
AlertEventType = Literal["new_mention", "new_episode"]


@dataclass(frozen=True, slots=True)
class AlertEventData:
    """New notification event generated during evaluation."""

    event: AccountAlertEvent
    rule: AccountAlertRule


def list_alert_rules(*, db: Session, user_id: int) -> list[AccountAlertRule]:
    """List a user's alert rules from newest to oldest."""
    return list(
        db.query(AccountAlertRule)
        .filter(AccountAlertRule.user_id == user_id)
        .order_by(AccountAlertRule.created_at.desc(), AccountAlertRule.id.desc())
        .all(),
    )


def create_alert_rule(
    *,
    db: Session,
    user_id: int,
    target_type: AlertTargetType,
    target_id: int,
    event_type: AlertEventType,
) -> AccountAlertRule | None:
    """Create an alert only for an account-linked public resource.

    A media rule locks its record first, so it is created either before a
    merge reads the record's rules or after the merge committed, on the
    surviving record only.
    """
    if target_type == "media" and target_id not in lock_media(
        db=db,
        media_ids=[target_id],
    ):
        return None
    if not _is_eligible_target(
        db=db,
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        event_type=event_type,
    ):
        return None
    existing = (
        db.query(AccountAlertRule)
        .filter(
            AccountAlertRule.user_id == user_id,
            AccountAlertRule.target_type == target_type,
            AccountAlertRule.target_id == target_id,
            AccountAlertRule.event_type == event_type,
        )
        .first()
    )
    if existing is not None:
        return existing
    rule = AccountAlertRule(
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
        event_type=event_type,
        baseline_count=_observed_count(
            db=db,
            target_type=target_type,
            target_id=target_id,
        ),
    )
    db.add(rule)
    db.flush()
    return rule


def set_alert_rule_enabled(
    *,
    db: Session,
    user_id: int,
    rule_id: int,
    enabled: bool,
) -> AccountAlertRule | None:
    """Enable or pause an alert rule belonging to the account."""
    rule = _get_rule(db=db, user_id=user_id, rule_id=rule_id)
    if rule is None:
        return None
    rule.enabled = enabled
    db.flush()
    return rule


def delete_alert_rule(*, db: Session, user_id: int, rule_id: int) -> bool:
    """Delete an account-owned alert rule.

    The rule's media record is locked first, then the rule, then its events
    are deleted. A rule a merge collapsed meanwhile no longer exists.
    """
    rule = _get_rule(db=db, user_id=user_id, rule_id=rule_id)
    if rule is None:
        return False
    if rule.target_type == "media":
        lock_media(db=db, media_ids=[rule.target_id])
    rule = (
        db.query(AccountAlertRule)
        .filter(
            AccountAlertRule.id == rule_id,
            AccountAlertRule.user_id == user_id,
        )
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if rule is None:
        return False
    db.query(AccountAlertEvent).filter(
        AccountAlertEvent.rule_id == rule.id,
    ).delete(synchronize_session=False)
    db.delete(rule)
    db.flush()
    return True


def evaluate_alert_rules(
    *,
    db: Session,
    user_id: int,
    now: datetime | None = None,
) -> list[AlertEventData]:
    """Generate one notification event per rule whose public count increased.

    The user's rules are locked before baselines are read, so evaluation cannot
    interleave with a canonical media merge or another evaluation and write back
    a stale baseline.
    """
    evaluated_at = now or datetime.now(UTC)
    generated: list[AlertEventData] = []
    lock_alert_rules(db=db, user_id=user_id)
    for rule in list_alert_rules(db=db, user_id=user_id):
        if not rule.enabled:
            continue
        observed_count = _observed_count(
            db=db,
            target_type=rule.target_type,
            target_id=rule.target_id,
        )
        rule.last_evaluated_at = evaluated_at
        if observed_count > rule.baseline_count:
            event = AccountAlertEvent(
                rule_id=rule.id,
                previous_count=rule.baseline_count,
                observed_count=observed_count,
                mention_ids_json=_reported_mention_ids(
                    db=db,
                    rule=rule,
                    count=observed_count - rule.baseline_count,
                ),
            )
            rule.baseline_count = observed_count
            db.add(event)
            db.flush()
            generated.append(AlertEventData(event=event, rule=rule))
    db.flush()
    return generated


def lock_alert_rules(*, db: Session, user_id: int) -> list[AccountAlertRule]:
    """Lock and reload one user's alert rules in ascending id order.

    Media merges lock the rules they reconcile in ``(user_id, id)`` order, which
    matches this order for one user and the scheduler's ascending user order, so
    the two never deadlock. SQLite has a single writer and ignores ``FOR UPDATE``.

    Returns:
        The user's rules, in id order.
    """
    return list(
        db.query(AccountAlertRule)
        .filter(AccountAlertRule.user_id == user_id)
        .order_by(AccountAlertRule.id.asc())
        .with_for_update()
        .populate_existing()
        .all(),
    )


def _get_rule(*, db: Session, user_id: int, rule_id: int) -> AccountAlertRule | None:
    """Load an account-owned alert rule."""
    return (
        db.query(AccountAlertRule)
        .filter(
            AccountAlertRule.id == rule_id,
            AccountAlertRule.user_id == user_id,
        )
        .first()
    )


def _is_eligible_target(
    *,
    db: Session,
    user_id: int,
    target_type: AlertTargetType,
    target_id: int,
    event_type: AlertEventType,
) -> bool:
    """Require rule targets to be linked public resources of the correct kind."""
    if target_type == "media" and event_type == "new_mention":
        return (
            db.query(AccountSavedMedia)
            .filter(
                AccountSavedMedia.user_id == user_id,
                AccountSavedMedia.media_id == target_id,
            )
            .first()
            is not None
        )
    if target_type == "podcast" and event_type == "new_episode":
        return (
            db.query(AccountFollowedPodcast)
            .filter(
                AccountFollowedPodcast.user_id == user_id,
                AccountFollowedPodcast.podcast_id == target_id,
            )
            .first()
            is not None
        )
    return False


def _reported_mention_ids(
    *,
    db: Session,
    rule: AccountAlertRule,
    count: int,
) -> list[int] | None:
    """Record which published mentions a media event reports: the newest ones.

    Digest delivery counts how many of them are still published, so a later
    takedown of these mentions, and only of these, reduces what is reported.
    """
    if rule.target_type != "media":
        return None
    newest = db.scalars(
        published_mentions(Mention.media_id == rule.target_id)
        .with_only_columns(Mention.id)
        .order_by(Mention.id.desc())
        .limit(count),
    )
    return [int(mention_id) for mention_id in newest]


def _observed_count(
    *,
    db: Session,
    target_type: str,
    target_id: int,
) -> int:
    """Measure published events represented by a rule target."""
    if target_type == "media":
        published: int = count_published_mentions(db, Mention.media_id == target_id)
        return published
    return int(db.query(Episode).filter(Episode.podcast_id == target_id).count())
