"""Account digest generation and delivery from alert events."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountDigest,
    AccountUser,
    Media,
    Mention,
    Podcast,
)
from podex.services.content_suppression import count_published_mentions
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
    """Deliver undigested alert events once and persist the delivery record.

    A media event reports how many of its own recorded mentions are still
    published. An event with none left is not delivered and stays pending;
    events are never deleted here.
    """
    pending = (
        db.query(AccountAlertEvent)
        .join(AccountAlertRule, AccountAlertRule.id == AccountAlertEvent.rule_id)
        .filter(
            AccountAlertRule.user_id == user.id,
            AccountAlertEvent.digest_id.is_(None),
        )
        .order_by(AccountAlertEvent.created_at.asc(), AccountAlertEvent.id.asc())
        .all()
    )
    deliverable: list[tuple[AccountAlertEvent, AccountAlertRule, int]] = []
    for event in pending:
        rule = (
            db.query(AccountAlertRule)
            .filter(AccountAlertRule.id == event.rule_id)
            .one()
        )
        count = _published_activity(db=db, event=event)
        if count > 0:
            deliverable.append((event, rule, count))
    if not deliverable:
        return None
    body_lines = ["New activity from your Podex alerts", ""]
    for _event, rule, count in deliverable:
        body_lines.append(_event_summary(db=db, rule=rule, count=count))
    body_lines.extend(["", "Manage your alerts in your Podex account."])
    events = [event for event, _, _ in deliverable]
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


def _published_activity(*, db: Session, event: AccountAlertEvent) -> int:
    """Return how much of an event's own activity is still published.

    Events that recorded their mentions count those still published; others
    (podcast events, and events raised before mentions were recorded) report
    their stored difference, as before.
    """
    recorded = event.mention_ids_json
    if recorded is None:
        stored: int = event.observed_count - event.previous_count
        return stored
    if not recorded:
        return 0
    published: int = count_published_mentions(db, Mention.id.in_(recorded))
    return published


def _event_summary(
    *,
    db: Session,
    rule: AccountAlertRule,
    count: int,
) -> str:
    """Render one human-readable notification line for ``count`` new items."""
    if rule.target_type == "media":
        media = db.query(Media).filter(Media.id == rule.target_id).one()
        return f"- {count} new mention{'s' if count != 1 else ''} of {media.title}"
    podcast = db.query(Podcast).filter(Podcast.id == rule.target_id).one()
    return f"- {count} new episode{'s' if count != 1 else ''} from {podcast.name}"
