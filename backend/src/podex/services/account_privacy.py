"""Self-serve account data export and deletion.

Deletion removes every row keyed to the account — sessions, magic-link
challenges, saves, follows, alert rules and their events, digests,
preferences, subscription, and quota usage — and finally the user row
itself. Nothing is anonymized in place: the audit log records the deletion
under an opaque numeric account id, which is the only reference retained.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

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
    MagicLinkToken,
    UserSession,
)


@dataclass(frozen=True, slots=True)
class AccountDeletionResultData:
    """Counts of rows removed by one account deletion."""

    user_id: int
    sessions: int
    magic_link_tokens: int
    saves: int
    follows: int
    alert_rules: int
    alert_events: int
    digests: int
    preferences: int
    subscriptions: int
    quota_usage: int


def export_account_data(*, db: Session, user: AccountUser) -> dict[str, Any]:
    """Assemble a machine-readable dump of everything stored for an account.

    Args:
        db: Database session.
        user: The authenticated account.

    Returns:
        JSON-serializable mapping of the account's stored data.
    """
    saves = (
        db.query(AccountSavedMedia)
        .filter(AccountSavedMedia.user_id == user.id)
        .order_by(AccountSavedMedia.id.asc())
        .all()
    )
    follows = (
        db.query(AccountFollowedPodcast)
        .filter(AccountFollowedPodcast.user_id == user.id)
        .order_by(AccountFollowedPodcast.id.asc())
        .all()
    )
    rules = (
        db.query(AccountAlertRule)
        .filter(AccountAlertRule.user_id == user.id)
        .order_by(AccountAlertRule.id.asc())
        .all()
    )
    rule_ids = [rule.id for rule in rules]
    events = (
        db.query(AccountAlertEvent)
        .filter(AccountAlertEvent.rule_id.in_(rule_ids))
        .order_by(AccountAlertEvent.id.asc())
        .all()
        if rule_ids
        else []
    )
    digests = (
        db.query(AccountDigest)
        .filter(AccountDigest.user_id == user.id)
        .order_by(AccountDigest.id.asc())
        .all()
    )
    preference = (
        db.query(AccountPreference).filter(AccountPreference.user_id == user.id).first()
    )
    subscription = (
        db.query(AccountSubscription)
        .filter(AccountSubscription.user_id == user.id)
        .first()
    )
    quotas = (
        db.query(AccountQuotaUsage)
        .filter(AccountQuotaUsage.user_id == user.id)
        .order_by(AccountQuotaUsage.id.asc())
        .all()
    )
    return {
        "account": {
            "email": user.email,
            "created_at": _iso(user.created_at),
            "last_signed_in_at": _iso(user.last_signed_in_at),
        },
        "saved_media": [
            {"media_id": row.media_id, "saved_at": _iso(row.created_at)}
            for row in saves
        ],
        "followed_podcasts": [
            {"podcast_id": row.podcast_id, "followed_at": _iso(row.created_at)}
            for row in follows
        ],
        "alert_rules": [
            {
                "target_type": rule.target_type,
                "target_id": rule.target_id,
                "event_type": rule.event_type,
                "enabled": rule.enabled,
                "created_at": _iso(rule.created_at),
            }
            for rule in rules
        ],
        "alert_events": [
            {
                "rule_id": event.rule_id,
                "previous_count": event.previous_count,
                "observed_count": event.observed_count,
                "created_at": _iso(event.created_at),
            }
            for event in events
        ],
        "digests": [
            {
                "subject": digest.subject,
                "body_text": digest.body_text,
                "event_count": digest.event_count,
                "created_at": _iso(digest.created_at),
                "delivered_at": _iso(digest.delivered_at),
            }
            for digest in digests
        ],
        "preferences": (
            {
                "digest_enabled": preference.digest_enabled,
                "digest_frequency": preference.digest_frequency,
            }
            if preference is not None
            else None
        ),
        "subscription": (
            {
                "tier": subscription.tier,
                "status": subscription.status,
                "current_period_ends_at": _iso(subscription.current_period_ends_at),
            }
            if subscription is not None
            else None
        ),
        "quota_usage": [
            {"period": row.period, "feature": row.feature, "units": row.units}
            for row in quotas
        ],
    }


def delete_account(*, db: Session, user: AccountUser) -> AccountDeletionResultData:
    """Delete an account and every row stored for it.

    Args:
        db: Database session.
        user: The authenticated account to delete.

    Returns:
        Counts of the rows removed, for the audit record.
    """
    user_id = user.id
    rule_ids = [
        row[0]
        for row in db.query(AccountAlertRule.id)
        .filter(AccountAlertRule.user_id == user_id)
        .all()
    ]
    alert_events = (
        db.query(AccountAlertEvent)
        .filter(AccountAlertEvent.rule_id.in_(rule_ids))
        .delete(synchronize_session=False)
        if rule_ids
        else 0
    )
    alert_rules = (
        db.query(AccountAlertRule)
        .filter(AccountAlertRule.user_id == user_id)
        .delete(synchronize_session=False)
    )
    digests = (
        db.query(AccountDigest)
        .filter(AccountDigest.user_id == user_id)
        .delete(synchronize_session=False)
    )
    saves = (
        db.query(AccountSavedMedia)
        .filter(AccountSavedMedia.user_id == user_id)
        .delete(synchronize_session=False)
    )
    follows = (
        db.query(AccountFollowedPodcast)
        .filter(AccountFollowedPodcast.user_id == user_id)
        .delete(synchronize_session=False)
    )
    preferences = (
        db.query(AccountPreference)
        .filter(AccountPreference.user_id == user_id)
        .delete(synchronize_session=False)
    )
    subscriptions = (
        db.query(AccountSubscription)
        .filter(AccountSubscription.user_id == user_id)
        .delete(synchronize_session=False)
    )
    quotas = (
        db.query(AccountQuotaUsage)
        .filter(AccountQuotaUsage.user_id == user_id)
        .delete(synchronize_session=False)
    )
    sessions = (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id)
        .delete(synchronize_session=False)
    )
    tokens = (
        db.query(MagicLinkToken)
        .filter(
            (MagicLinkToken.user_id == user_id) | (MagicLinkToken.email == user.email),
        )
        .delete(synchronize_session=False)
    )
    db.delete(user)
    db.flush()
    return AccountDeletionResultData(
        user_id=user_id,
        sessions=sessions,
        magic_link_tokens=tokens,
        saves=saves,
        follows=follows,
        alert_rules=alert_rules,
        alert_events=alert_events,
        digests=digests,
        preferences=preferences,
        subscriptions=subscriptions,
        quota_usage=quotas,
    )


def _iso(value: Any) -> str | None:
    """Render an optional datetime as ISO-8601."""
    return value.isoformat() if value is not None else None
