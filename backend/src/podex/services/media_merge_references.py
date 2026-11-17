"""Account and extraction references moved during canonical media merges.

Every row that points at a merged-away media record is resolved before the
source is deleted:

- Mention candidates, their provenance snapshots and review items move to the
  surviving record, so extraction history keeps resolving to the canonical work.
- Mention-pair takedown suppressions move to the survivor and collapse to one
  per ``(episode, media)`` pair, so a takedown of a mention of the merged-away
  record keeps covering mentions of the same work in that episode.
- Saved media move to the survivor and collapse to one save per account.
- Media alert rules move to the survivor and collapse to one rule per account
  and event type. Alert events from a collapsed rule move to the surviving rule,
  so pending events still render and delivered events keep their digest.

Alert baselines count activity an account has already seen. After a merge the
survivor's mention count includes both records, so each surviving rule's
baseline becomes the sum of what the account has seen on each side. A side with
a rule contributes ``min(baseline, current count)``, so mentions removed since
the rule was last evaluated never cancel unseen activity on the other side; a
side without a rule counts as fully seen. Merging identities therefore never
reports historical mentions as new and never hides unseen ones.

The merge takes its locks in the order every writer of media references
follows (ADR 0020), before it reads any state:

1. both media rows ``FOR UPDATE``, ascending id, in one statement;
2. the podcast rows of every episode that mentions either record ``FOR
   SHARE`` (the lock the mention-moving flush would take later);
3. the alert rules it reconciles, in ``(user_id, id)`` order, the same order
   alert evaluation locks a user's rules;
4. then it changes alert events, saves and the other references.

The suppression change counter is bumped last, by the caller. SQLite has a
single writer and ignores the lock clauses.
"""

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountSavedMedia,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    Media,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    ReviewItem,
)
from podex.models.content_suppression import suppressed_clause, suppression_key
from podex.models.suppression_guard import lock_podcasts_of_episodes
from podex.services.content_suppression import (
    count_published_mentions,
    delete_mentions,
    pair_suppressions_of,
)
from podex.services.media_locks import lock_media

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


def lock_merge_pair(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> tuple[Media | None, Media | None]:
    """Lock and reload the merge source and target, then their podcasts.

    Both records are locked ``FOR UPDATE`` in ascending id order. Concurrent
    merges, and every writer of rows referencing either record, wait here,
    then read the state the earlier transaction committed, including a source
    it already deleted. The podcasts of the episodes mentioning either record
    are then locked ``FOR SHARE``, before any alert rule, as ADR 0020 orders
    them; no mention of either record can be added while the records are
    held, so that set is complete.

    Args:
        db: Database session.
        source_media_id: Media record being merged away.
        target_media_id: Surviving canonical media record.

    Returns:
        The locked source and target, each ``None`` when it no longer exists.
    """
    locked = lock_media(
        db=db,
        media_ids=(source_media_id, target_media_id),
        exclusive=True,
    )
    source, target = locked.get(source_media_id), locked.get(target_media_id)
    if source is not None and target is not None:
        lock_podcasts_of_episodes(
            session=db,
            episode_ids=db.scalars(
                select(Mention.episode_id)
                .where(Mention.media_id.in_((source_media_id, target_media_id)))
                .distinct(),
            ),
        )
    return source, target


def delete_mentions_covered_after_merge(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> int:
    """Delete mentions of either record that a suppression of either covers.

    After the merge both records are one work, so a suppression of
    ``(episode, source)`` or ``(episode, target)`` covers every mention of
    either record in that episode. Those mentions are deleted, as a mention
    takedown deletes its whole pair, instead of refusing the merge. Call it
    before alert baselines are read, so baselines count what stays published.

    Returns:
        The number of mentions deleted.
    """
    covered = or_(
        *(
            suppressed_clause(
                scope=ContentSuppressionScope.MENTIONS,
                episode_id=Mention.episode_id,
                media_id=media_id,
            )
            for media_id in (source_media_id, target_media_id)
        ),
    )
    mentions = (
        db.query(Mention)
        .filter(Mention.media_id.in_((source_media_id, target_media_id)), covered)
        .order_by(Mention.id)
        .all()
    )
    return len(delete_mentions(db, mentions))


def move_media_references(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> None:
    """Move account and extraction references from a merged source to the target.

    Call after ``lock_merge_pair`` and before mentions move, so alert
    baselines see pre-merge mention counts. The alert rules are locked and
    validated first, before any row changes, so a refused merge mutates
    nothing. Mentions a carried suppression covers are deleted next, after
    the rule locks (their deletion edits alert events, which follow rules in
    the lock order) and before baselines are read.

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
    delete_mentions_covered_after_merge(
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
    _move_mention_pair_suppressions(
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


def _move_mention_pair_suppressions(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> None:
    """Repoint pair suppressions at the target, keeping one per pair and scope.

    The key encodes the media id, so a moved row gets the target's key; when
    the target pair is already suppressed the source row is dropped instead.
    """
    existing_keys = {
        key
        for (key,) in db.query(ContentSuppression.suppression_key)
        .filter(ContentSuppression.media_id == target_media_id)
        .all()
    }
    for suppression, episode_id in pair_suppressions_of(db, source_media_id):
        moved_key = suppression_key(
            subject_type=ContentSuppressionSubjectType.MENTION_PAIR,
            scope=ContentSuppressionScope(suppression.scope),
            subject_ids=(episode_id, target_media_id),
        )
        if moved_key in existing_keys:
            db.delete(suppression)
            continue
        suppression.media_id = target_media_id
        suppression.suppression_key = moved_key
        existing_keys.add(moved_key)


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


def _plan_media_alert_rules(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> _AlertRulePlan:
    """Lock both records' media alert rules and pair each source rule.

    Rules are locked in ``(user_id, id)`` order and reloaded, so a concurrent
    alert evaluation finishes before the merge reads baselines, or waits for the
    merge to commit before it reads them.

    Raises:
        ValueError: If a colliding pair disagrees on enabled state.
    """
    rules = (
        db.query(AccountAlertRule)
        .filter(
            AccountAlertRule.target_type == MEDIA_ALERT_TARGET_TYPE,
            AccountAlertRule.target_id.in_((source_media_id, target_media_id)),
        )
        .order_by(AccountAlertRule.user_id.asc(), AccountAlertRule.id.asc())
        .with_for_update()
        .populate_existing()
        .all()
    )
    target_rules = [rule for rule in rules if rule.target_id == target_media_id]
    target_rules_by_key = {
        (rule.user_id, rule.event_type): rule for rule in target_rules
    }
    pairs: list[_AlertRulePair] = []
    for source_rule in (rule for rule in rules if rule.target_id == source_media_id):
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
    """Count published mentions of one media record, as alert evaluation does."""
    published: int = count_published_mentions(db, Mention.media_id == media_id)
    return published


def _seen_count(*, rule: AccountAlertRule | None, mention_count: int) -> int:
    """Count one side's mentions that the rule's account has already seen.

    Args:
        rule: Rule watching this side, or ``None`` when the account has none.
        mention_count: Current published mention count on this side.

    Returns:
        The rule baseline capped at the current count, or the full count when
        no rule watches this side.
    """
    if rule is None:
        return mention_count
    seen: int = rule.baseline_count
    return min(seen, mention_count)


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
        source_seen = _seen_count(rule=source_rule, mention_count=source_count)
        if target_rule is None:
            source_rule.target_id = target_media_id
            source_rule.baseline_count = source_seen + target_count
            continue
        paired_target_rule_ids.add(target_rule.id)
        target_rule.baseline_count = source_seen + _seen_count(
            rule=target_rule,
            mention_count=target_count,
        )
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
            target_rule.baseline_count = source_count + _seen_count(
                rule=target_rule,
                mention_count=target_count,
            )

    for source_rule in collapsed_rules:
        db.delete(source_rule)


def merge_verification_sources(
    *,
    source_values: list[str] | None,
    target_values: list[str] | None,
) -> list[str] | None:
    """Merge verification source lists while preserving target precedence.

    Args:
        source_values: Verification sources from the source media record.
        target_values: Verification sources from the target media record.

    Returns:
        Combined verification source list when any values exist.
    """
    if not source_values and not target_values:
        return target_values

    combined = list(target_values or [])
    for value in source_values or []:
        if value not in combined:
            combined.append(value)
    return combined
