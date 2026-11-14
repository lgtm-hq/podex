"""Account and extraction references moved during canonical media merges.

Every row that points at a merged-away media record is resolved before the
source is deleted:

- Mention candidates, their provenance snapshots and review items move to the
  surviving record, so extraction history keeps resolving to the canonical work.
- Saved media move to the survivor and collapse to one save per account.
- Media alert rules move to the survivor and collapse to one rule per account
  and event type. Alert events from a collapsed rule move to the surviving rule,
  so pending events still render and delivered events keep their digest.

Alert baselines count activity an account has already seen. After a merge the
survivor's mention count includes both records, so each surviving rule's
baseline becomes the sum of what the account had seen on each side: the rule's
own baseline where a rule existed, otherwise that side's full mention count.
Merging identities therefore never reports historical mentions as new.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountSavedMedia,
    Media,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    ReviewItem,
)

MEDIA_ALERT_TARGET_TYPE = "media"


@dataclass(frozen=True, slots=True)
class _AlertRulePair:
    """Source rule and the survivor rule it collides with, if any."""

    source_rule: AccountAlertRule
    target_rule: AccountAlertRule | None


@dataclass(frozen=True, slots=True)
class _AlertRulePlan:
    """Media alert rules on both records, loaded before any rule changes."""

    pairs: list[_AlertRulePair]
    target_rules: list[AccountAlertRule]


def move_media_references(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> None:
    """Move account and extraction references from a merged source to the target.

    Call before mentions move so alert baselines see pre-merge mention counts.
    Validation runs before any row changes, so a refused merge mutates nothing.

    Args:
        db: Database session.
        source_media: Media record being merged away.
        target_media: Surviving canonical media record.

    Raises:
        ValueError: If one account has alert rules on both records that disagree
            on enabled state.
    """
    rule_plan = _plan_media_alert_rules(
        db=db,
        source_media_id=source_media.id,
        target_media_id=target_media.id,
    )
    _move_extraction_references(
        db=db,
        source_media=source_media,
        target_media=target_media,
    )
    _move_saved_media(
        db=db,
        source_media_id=source_media.id,
        target_media_id=target_media.id,
    )
    _move_media_alert_rules(
        db=db,
        rule_plan=rule_plan,
        source_media_id=source_media.id,
        target_media_id=target_media.id,
    )
    db.flush()


def _move_extraction_references(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> None:
    """Point candidates, provenance snapshots and review items at the target."""
    for candidate in (
        db.query(MentionCandidate)
        .filter(MentionCandidate.media_id == source_media.id)
        .all()
    ):
        candidate.media = target_media
    for provenance in (
        db.query(MentionCandidateProvenance)
        .filter(MentionCandidateProvenance.media_id == source_media.id)
        .all()
    ):
        provenance.media = target_media
    for review_item in (
        db.query(ReviewItem).filter(ReviewItem.target_media_id == source_media.id).all()
    ):
        review_item.target_media = target_media


def _move_saved_media(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> None:
    """Move saves to the target, keeping the existing save when both exist."""
    target_user_ids = {
        user_id
        for (user_id,) in db.query(AccountSavedMedia.user_id)
        .filter(AccountSavedMedia.media_id == target_media_id)
        .all()
    }
    for save in (
        db.query(AccountSavedMedia)
        .filter(AccountSavedMedia.media_id == source_media_id)
        .all()
    ):
        if save.user_id in target_user_ids:
            db.delete(save)
        else:
            save.media_id = target_media_id


def _media_alert_rules(*, db: Session, media_id: int) -> list[AccountAlertRule]:
    """Load alert rules targeting one media record."""
    return list(
        db.query(AccountAlertRule)
        .filter(
            AccountAlertRule.target_type == MEDIA_ALERT_TARGET_TYPE,
            AccountAlertRule.target_id == media_id,
        )
        .order_by(AccountAlertRule.id.asc())
        .all(),
    )


def _plan_media_alert_rules(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> _AlertRulePlan:
    """Pair each source rule with the survivor rule sharing its account and event.

    Raises:
        ValueError: If a colliding pair disagrees on enabled state.
    """
    target_rules = _media_alert_rules(db=db, media_id=target_media_id)
    target_rules_by_key = {
        (rule.user_id, rule.event_type): rule for rule in target_rules
    }
    pairs: list[_AlertRulePair] = []
    for source_rule in _media_alert_rules(db=db, media_id=source_media_id):
        target_rule = target_rules_by_key.get(
            (source_rule.user_id, source_rule.event_type),
        )
        if target_rule is not None and target_rule.enabled != source_rule.enabled:
            raise ValueError(
                "Alert rules on the source and target media disagree on enabled "
                "state for the same account",
            )
        pairs.append(_AlertRulePair(source_rule=source_rule, target_rule=target_rule))
    return _AlertRulePlan(pairs=pairs, target_rules=target_rules)


def _mention_count(*, db: Session, media_id: int) -> int:
    """Count published mentions of one media record."""
    return int(db.query(Mention).filter(Mention.media_id == media_id).count())


def _move_media_alert_rules(
    *,
    db: Session,
    rule_plan: _AlertRulePlan,
    source_media_id: int,
    target_media_id: int,
) -> None:
    """Move or consolidate media alert rules and reconcile their baselines."""
    source_count = _mention_count(db=db, media_id=source_media_id)
    target_count = _mention_count(db=db, media_id=target_media_id)
    paired_target_rule_ids: set[int] = set()
    collapsed_rules: list[AccountAlertRule] = []

    for pair in rule_plan.pairs:
        source_rule = pair.source_rule
        target_rule = pair.target_rule
        if target_rule is None:
            source_rule.target_id = target_media_id
            source_rule.baseline_count += target_count
            continue
        paired_target_rule_ids.add(target_rule.id)
        target_rule.baseline_count += source_rule.baseline_count
        if target_rule.last_evaluated_at is None:
            target_rule.last_evaluated_at = source_rule.last_evaluated_at
        db.query(AccountAlertEvent).filter(
            AccountAlertEvent.rule_id == source_rule.id,
        ).update(
            {AccountAlertEvent.rule_id: target_rule.id},
            synchronize_session="fetch",
        )
        collapsed_rules.append(source_rule)

    for target_rule in rule_plan.target_rules:
        if target_rule.id not in paired_target_rule_ids:
            target_rule.baseline_count += source_count

    for source_rule in collapsed_rules:
        db.delete(source_rule)
