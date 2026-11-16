"""Lookups and recording for durable takedown suppressions.

Every check here, every public mention read and the flush guard in
``podex.models.suppression_guard`` evaluate ``suppressed_clause``, so they
agree on what a suppression covers. See ADR 0014.
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    Episode,
    Mention,
    Podcast,
)
from podex.models.content_suppression import suppressed_clause, suppression_key

__all__ = [
    "ContentSuppressedError",
    "count_published_mentions",
    "lock_podcasts_for_takedown",
    "is_episode_suppressed",
    "is_mention_pair_suppressed",
    "published_mention_clause",
    "published_mentions",
    "record_suppression",
    "require_episode_not_suppressed",
    "suppressed_episode_ids",
]


def published_mention_clause() -> ColumnElement[bool]:
    """Return the filter every public read of ``Mention`` rows must apply.

    Returns:
        SQL that is true for mentions no ``mentions`` suppression covers.
    """
    published: ColumnElement[bool] = ~suppressed_clause(
        scope=ContentSuppressionScope.MENTIONS,
        episode_id=Mention.episode_id,
        media_id=Mention.media_id,
    )
    return published


def published_mentions(*criteria: ColumnElement[bool]) -> Select[tuple[Mention]]:
    """Return a ``Mention`` select limited to published, unsuppressed rows.

    Args:
        *criteria: Further filters, such as the parent episode or media.

    Returns:
        A select callers order and page.
    """
    return select(Mention).where(published_mention_clause(), *criteria)


def count_published_mentions(db: Session, *criteria: ColumnElement[bool]) -> int:
    """Count published, unsuppressed mentions matching ``criteria``.

    Args:
        db: Database session.
        *criteria: Further filters, such as the parent episode or media.

    Returns:
        The matching mention count.
    """
    statement = (
        select(func.count())
        .select_from(Mention)
        .where(published_mention_clause(), *criteria)
    )
    return int(db.execute(statement).scalar_one())


def is_episode_suppressed(
    *,
    db: Session,
    episode_id: int,
    scope: ContentSuppressionScope,
) -> bool:
    """Return whether ``scope`` content of an episode is suppressed.

    Args:
        db: Database session.
        episode_id: Episode to test.
        scope: Suppression scope to test.

    Returns:
        ``True`` when the episode or its podcast is suppressed for ``scope``.
    """
    return bool(
        db.execute(
            select(suppressed_clause(scope=scope, episode_id=episode_id)),
        ).scalar_one(),
    )


def is_mention_pair_suppressed(*, db: Session, episode_id: int, media_id: int) -> bool:
    """Return whether a mention of ``media_id`` in ``episode_id`` may not exist.

    Args:
        db: Database session.
        episode_id: Episode of the mention.
        media_id: Media of the mention.

    Returns:
        ``True`` when the pair, the episode or its podcast is suppressed for
        mentions.
    """
    return bool(
        db.execute(
            select(
                suppressed_clause(
                    scope=ContentSuppressionScope.MENTIONS,
                    episode_id=episode_id,
                    media_id=media_id,
                ),
            ),
        ).scalar_one(),
    )


def suppressed_episode_ids(
    *,
    db: Session,
    scope: ContentSuppressionScope,
    episode_ids: Iterable[int],
) -> set[int]:
    """Return which of ``episode_ids`` are suppressed for ``scope``, in one query.

    Args:
        db: Database session.
        scope: Suppression scope to test.
        episode_ids: Episodes a rebuild is about to process.

    Returns:
        The suppressed subset.
    """
    wanted = sorted(set(episode_ids))
    if not wanted:
        return set()
    rows = db.execute(
        select(Episode.id).where(
            Episode.id.in_(wanted),
            suppressed_clause(scope=scope, episode_id=Episode.id),
        ),
    ).all()
    return {int(row[0]) for row in rows}


def require_episode_not_suppressed(
    *,
    db: Session,
    episode_id: int,
    scope: ContentSuppressionScope,
) -> None:
    """Refuse work on suppressed content before any provider sees its text.

    The flush guard refuses the write anyway; checking first also keeps a
    suppressed transcript from reaching an embedding or summary provider.

    Args:
        db: Database session.
        episode_id: Episode about to be processed.
        scope: Suppression scope the work would write.

    Raises:
        ContentSuppressedError: If the episode is suppressed for ``scope``.
    """
    if is_episode_suppressed(db=db, episode_id=episode_id, scope=scope):
        raise ContentSuppressedError(scope=scope, episode_id=episode_id)


def lock_podcasts_for_takedown(*, db: Session, podcast_ids: Iterable[int]) -> None:
    """Lock podcast rows ``FOR UPDATE`` before a takedown reads what to remove.

    Guarded writers lock the same rows ``FOR SHARE`` before writing, so a
    writer either commits before the takedown reads (and its rows are then
    deleted) or waits and then sees the suppression. Rows are locked in
    ascending id order, the order writers use. SQLite ignores the clause.

    Args:
        db: Database session running the takedown.
        podcast_ids: Podcasts whose content the takedown covers.
    """
    wanted = sorted(set(podcast_ids))
    if wanted:
        db.execute(
            select(Podcast.id)
            .where(Podcast.id.in_(wanted))
            .order_by(Podcast.id)
            .with_for_update(),
        ).all()


def record_suppression(
    *,
    db: Session,
    takedown_request_id: int,
    subject_type: ContentSuppressionSubjectType,
    scope: ContentSuppressionScope,
    subject_ids: tuple[int, ...],
) -> ContentSuppression:
    """Record one suppression, or return the one already recorded for its key.

    Args:
        db: Database session running the takedown.
        takedown_request_id: Approved takedown the suppression comes from.
        subject_type: What the suppression covers.
        scope: Which content it suppresses.
        subject_ids: Podcast id, episode id, or episode and media ids.

    Returns:
        The stored suppression row.
    """
    key = suppression_key(
        subject_type=subject_type,
        scope=scope,
        subject_ids=subject_ids,
    )
    existing = (
        db.query(ContentSuppression)
        .filter(ContentSuppression.suppression_key == key)
        .one_or_none()
    )
    if existing is not None:
        return existing
    row = ContentSuppression(
        suppression_key=key,
        takedown_request_id=takedown_request_id,
        subject_type=subject_type.value,
        scope=scope.value,
    )
    if subject_type == ContentSuppressionSubjectType.PODCAST:
        (row.podcast_id,) = subject_ids
    elif subject_type == ContentSuppressionSubjectType.EPISODE:
        (row.episode_id,) = subject_ids
    else:
        row.episode_id, row.media_id = subject_ids
    db.add(row)
    db.flush()
    return row
