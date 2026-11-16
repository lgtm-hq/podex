"""Lookups and recording for durable takedown suppressions.

Every check here, every public mention read and the flush guard in
``podex.models.suppression_guard`` evaluate ``suppressed_clause``, so they
agree on what a suppression covers. See ADR 0015.
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, Select, func, select, text
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    Mention,
    Podcast,
)
from podex.models.content_suppression import (
    SUPPRESSION_BATCH_SIZE,
    suppressed_clause,
    suppressed_episodes_statement,
    suppression_key,
)
from podex.models.suppression_guard import lock_media_rows, lock_podcasts_of_episodes

__all__ = [
    "ContentSuppressedError",
    "authorize_episode_text",
    "authorize_media_summary_text",
    "count_published_mentions",
    "is_episode_suppressed",
    "is_mention_pair_suppressed",
    "lock_episode_text",
    "lock_media_for_takedown",
    "lock_podcasts_for_takedown",
    "published_mention_clause",
    "published_mentions",
    "record_suppression",
    "summary_source_mentions",
    "suppressed_episode_ids",
    "suppression_version",
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
    """Return which of ``episode_ids`` are suppressed for ``scope``, batched.

    Args:
        db: Database session.
        scope: Suppression scope to test.
        episode_ids: Episodes a rebuild is about to process.

    Returns:
        The suppressed subset.
    """
    wanted = sorted(set(episode_ids))
    found: set[int] = set()
    for start in range(0, len(wanted), SUPPRESSION_BATCH_SIZE):
        batch = wanted[start : start + SUPPRESSION_BATCH_SIZE]
        found.update(
            int(episode_id)
            for episode_id in db.scalars(
                suppressed_episodes_statement(scope=scope, episode_ids=batch),
            )
        )
    return found


def summary_source_mentions(*criteria: ColumnElement[bool]) -> Select[tuple[Mention]]:
    """Return mentions whose context may feed a derived summary.

    A mention qualifies when it is published and its episode's derivatives are
    not suppressed: a media summary is itself a derivative of every episode
    whose mention context it quotes.

    Args:
        *criteria: Further filters, such as the media record.

    Returns:
        A select callers order.
    """
    return published_mentions(
        ~suppressed_clause(
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_id=Mention.episode_id,
        ),
        *criteria,
    )


def _hold_sqlite_write_lock(db: Session) -> None:
    """Take SQLite's database write lock for the rest of the transaction.

    SQLite ignores row locks. A write statement takes its single write lock,
    which a takedown also needs, so holding it from authorisation until the
    transaction ends gives SQLite the same order PostgreSQL gets from rows.
    The statement changes no row.
    """
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("UPDATE content_suppressions SET id = id WHERE 0 = 1"))


def lock_episode_text(*, db: Session, episode_ids: Iterable[int]) -> None:
    """Take the lock that keeps a takedown out while episode text is in use.

    On PostgreSQL these are the writers' ``FOR SHARE`` locks on the episodes'
    podcast rows; on SQLite, the database write lock. Both last until the
    transaction ends.

    Args:
        db: Session of the transaction that will read the text.
        episode_ids: Episodes whose text is about to be read.
    """
    _hold_sqlite_write_lock(db)
    lock_podcasts_of_episodes(session=db, episode_ids=episode_ids)


def authorize_episode_text(
    *,
    db: Session,
    episode_ids: Iterable[int],
    scope: ContentSuppressionScope = ContentSuppressionScope.DERIVATIVES,
) -> None:
    """Lock, then check, before any episode text is read for a provider.

    Takes the writers' locks (podcast rows ``FOR SHARE``; SQLite's write lock),
    which a takedown waits for until this transaction ends, then checks the
    episodes under them. Call it before reading the text and keep the
    transaction open until the provider has returned, so a takedown either
    completed before the text was read or starts after the call is over.

    Args:
        db: Session of the transaction that will call the provider.
        episode_ids: Episodes whose text is about to be read.
        scope: Suppression scope the work would write.

    Raises:
        ContentSuppressedError: If an approved takedown covers any episode.
    """
    wanted = set(episode_ids)
    lock_episode_text(db=db, episode_ids=wanted)
    suppressed = sorted(suppressed_episode_ids(db=db, scope=scope, episode_ids=wanted))
    if suppressed:
        raise ContentSuppressedError(scope=scope, episode_id=suppressed[0])


def authorize_media_summary_text(*, db: Session, media_id: int) -> None:
    """Lock a media record and the episodes its summary would quote.

    Locks follow the shared order (the media row, then podcast rows), and the
    source is read afterwards through ``summary_source_mentions``, so it
    reflects every takedown committed before the locks were granted, and none
    can commit until the provider call and its transaction are over.

    Args:
        db: Session of the transaction that will call the provider.
        media_id: Media record about to be summarised.
    """
    lock_media_rows(session=db, media_ids=[media_id])
    episode_ids = db.scalars(
        select(Mention.episode_id).where(Mention.media_id == media_id).distinct(),
    ).all()
    lock_episode_text(db=db, episode_ids=episode_ids)


def lock_media_for_takedown(*, db: Session, media_ids: Iterable[int]) -> None:
    """Lock media rows ``FOR KEY SHARE`` before the takedown's podcast lock.

    The shared lock order puts media rows before podcast rows, as the media
    merge does; the key-share lock orders the takedown after a merge holding
    the record ``FOR UPDATE`` without blocking other key-share holders.

    Args:
        db: Session running the takedown.
        media_ids: Media records whose mentions or summaries it will touch.
    """
    lock_media_rows(session=db, media_ids=media_ids)


def lock_podcasts_for_takedown(*, db: Session, podcast_ids: Iterable[int]) -> None:
    """Lock podcast rows ``FOR UPDATE`` before a takedown reads what to remove.

    Guarded writers lock the same rows ``FOR SHARE`` before writing, so a
    writer either commits before the takedown reads (and its rows are then
    deleted) or waits and then sees the suppression. Rows are locked in
    ascending id order, the order writers use. SQLite ignores the clause.

    Args:
        db: Session running the takedown.
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


def suppression_version(db: Session) -> tuple[int, int]:
    """Return a value that changes whenever suppression rows change.

    Caches of public aggregates key on it, so the first read after a takedown
    commits misses the cache in every process.

    Args:
        db: Database session.

    Returns:
        The number of suppression rows and the highest id.
    """
    count, highest = db.execute(
        select(func.count(), func.coalesce(func.max(ContentSuppression.id), 0)),
    ).one()
    return int(count), int(highest)


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
