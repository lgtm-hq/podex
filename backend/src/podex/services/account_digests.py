"""Account digest generation and delivery from alert events."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountDigest,
    AccountUser,
    Media,
    Podcast,
)
from podex.services.notification_delivery import DigestSender


def list_account_digests(*, db: Session, user_id: int) -> list[AccountDigest]:
    """List previously delivered digests for an account."""
    return list(
        db.query(AccountDigest)
        .filter(AccountDigest.user_id == user_id)
        .order_by(AccountDigest.created_at.desc(), AccountDigest.id.desc())
        .all(),
    )


def send_pending_digest(
    *,
    db: Session,
    user: AccountUser,
    sender: DigestSender,
    now: datetime | None = None,
) -> AccountDigest | None:
    """Deliver undigested alert events once and persist the delivery record."""
    events = (
        db.query(AccountAlertEvent)
        .join(AccountAlertRule, AccountAlertRule.id == AccountAlertEvent.rule_id)
        .filter(
            AccountAlertRule.user_id == user.id,
            AccountAlertEvent.digest_id.is_(None),
        )
        .order_by(AccountAlertEvent.created_at.asc(), AccountAlertEvent.id.asc())
        .all()
    )
    if not events:
        return None
    body_lines = ["New activity from your Podex alerts", ""]
    for event in events:
        rule = (
            db.query(AccountAlertRule)
            .filter(AccountAlertRule.id == event.rule_id)
            .one()
        )
        body_lines.append(_event_summary(db=db, rule=rule, event=event))
    body_lines.extend(["", "Manage your alerts in your Podex account."])
    subject = f"Podex digest: {len(events)} new update{'s' if len(events) != 1 else ''}"
    digest = AccountDigest(
        user_id=user.id,
        subject=subject,
        body_text="\n".join(body_lines),
        event_count=len(events),
    )
    db.add(digest)
    db.flush()
    sender.send_digest(email=user.email, subject=subject, body_text=digest.body_text)
    delivered_at = now or datetime.now(UTC)
    digest.delivered_at = delivered_at
    for event in events:
        event.digest_id = digest.id
    db.flush()
    return digest


def _event_summary(
    *,
    db: Session,
    rule: AccountAlertRule,
    event: AccountAlertEvent,
) -> str:
    """Render one human-readable notification line."""
    if rule.target_type == "media":
        media = db.query(Media).filter(Media.id == rule.target_id).one()
        count = event.observed_count - event.previous_count
        return f"- {count} new mention{'s' if count != 1 else ''} of {media.title}"
    podcast = db.query(Podcast).filter(Podcast.id == rule.target_id).one()
    count = event.observed_count - event.previous_count
    return f"- {count} new episode{'s' if count != 1 else ''} from {podcast.name}"
