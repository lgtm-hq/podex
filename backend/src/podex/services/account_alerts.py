"""Authenticated account alert rules and deterministic evaluation."""

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
    """Create an alert only for an account-linked public resource."""
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
    """Delete an account-owned alert rule."""
    rule = _get_rule(db=db, user_id=user_id, rule_id=rule_id)
    if rule is None:
        return False
    db.delete(rule)
    db.flush()
    return True


def evaluate_alert_rules(
    *,
    db: Session,
    user_id: int,
    now: datetime | None = None,
) -> list[AlertEventData]:
    """Generate one notification event per rule whose public count increased."""
    evaluated_at = now or datetime.now(UTC)
    generated: list[AlertEventData] = []
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
            )
            rule.baseline_count = observed_count
            db.add(event)
            db.flush()
            generated.append(AlertEventData(event=event, rule=rule))
    db.flush()
    return generated


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


def _observed_count(
    *,
    db: Session,
    target_type: str,
    target_id: int,
) -> int:
    """Measure published events represented by a rule target."""
    if target_type == "media":
        return int(db.query(Mention).filter(Mention.media_id == target_id).count())
    return int(db.query(Episode).filter(Episode.podcast_id == target_id).count())
