"""Lookups and recording for durable takedown suppressions.

Every check here, every public mention read and the flush guard in
``podex.models.suppression_guard`` evaluate ``suppressed_clause``, so they
agree on what a suppression covers. See ADR 0015.
"""

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    ContentSuppressionVersion,
    GraphTriple,
    Mention,
    MentionCandidate,
    Podcast,
)
from podex.models.content_suppression import (
    SUPPRESSION_BATCH_SIZE,
    suppressed_clause,
    suppressed_episodes_statement,
    suppression_key,
)
from podex.models.suppression_guard import lock_media_rows, lock_podcasts_of_episodes
from podex.models.suppression_identity import (
    bump_suppression_version,
    hold_sqlite_write_lock,
)

__all__ = [
    "ContentSuppressedError",
    "RetryableLockError",
    "authorize_episode_text",
    "authorize_media_summary_text",
    "bump_suppression_version",
    "copy_pair_suppressions",
    "count_published_mentions",
    "delete_mentions",
    "is_episode_suppressed",
    "is_mention_pair_suppressed",
    "lock_episode_text",
    "lock_media_for_takedown",
    "lock_podcasts_for_takedown",
    "mentioning_episodes",
    "pair_suppressions_of",
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


def lock_episode_text(*, db: Session, episode_ids: Iterable[int]) -> None:
    """Take the lock that keeps a takedown out while episode text is in use.

    On PostgreSQL these are the writers' ``FOR SHARE`` locks on the episodes'
    podcast rows; on SQLite, the database write lock. Both last until the
    transaction ends.

    Args:
        db: Session of the transaction that will read the text.
        episode_ids: Episodes whose text is about to be read.
    """
    hold_sqlite_write_lock(db)
    lock_podcasts_of_episodes(session=db, episode_ids=episode_ids)


class RetryableLockError(ValueError):
    """Content changed, or a lock was busy, while locks were being taken.

    Nothing was written; the operation can be retried as it is. Routes map
    ``ValueError`` to 409.
    """


_STABLE_READ_ATTEMPTS = 3


def _refuse_if_suppressed(
    db: Session,
    episode_ids: set[int],
    scope: ContentSuppressionScope,
) -> None:
    suppressed = sorted(
        suppressed_episode_ids(db=db, scope=scope, episode_ids=episode_ids)
    )
    if suppressed:
        raise ContentSuppressedError(scope=scope, episode_id=suppressed[0])


@contextmanager
def _released_on_refusal(db: Session) -> Iterator[None]:
    """Release the locks taken inside the block if it refuses.

    PostgreSQL releases row locks taken in a savepoint that rolls back. SQLite
    keeps its write lock until the transaction ends, so when this block began
    the write transaction, the refusal ends it; earlier writes of the caller
    would keep it, and are left alone.
    """
    if db.get_bind().dialect.name == "sqlite":
        dbapi: Any = db.connection().connection.dbapi_connection
        started = not dbapi.in_transaction
        try:
            yield
        except (ContentSuppressedError, RetryableLockError):
            if started:
                dbapi.rollback()
            raise
        return
    savepoint = db.begin_nested()
    try:
        yield
    except (ContentSuppressedError, RetryableLockError):
        savepoint.rollback()
        raise
    savepoint.commit()


def authorize_episode_text(
    *,
    db: Session,
    episode_ids: Iterable[int],
    scope: ContentSuppressionScope = ContentSuppressionScope.DERIVATIVES,
) -> None:
    """Check, lock, then check again, before episode text is read for a provider.

    The first check runs without a lock, so the usual refusal (a takedown that
    committed earlier) holds nothing. Then the writers' locks are taken
    (podcast rows ``FOR SHARE``; SQLite's write lock), which a takedown waits
    for until this transaction ends, and the episodes are checked again under
    them; a refusal there releases the locks. Call it before reading the text
    and keep the transaction open until the provider has returned, so a
    takedown either completed before the text was read or starts after the
    call is over.

    Args:
        db: Session of the transaction that will call the provider.
        episode_ids: Episodes whose text is about to be read.
        scope: Suppression scope the work would write.

    Raises:
        ContentSuppressedError: If an approved takedown covers any episode.
    """
    wanted = set(episode_ids)
    _refuse_if_suppressed(db, wanted, scope)
    with _released_on_refusal(db):
        lock_episode_text(db=db, episode_ids=wanted)
        _refuse_if_suppressed(db, wanted, scope)


def mentioning_episodes(db: Session, media_id: int) -> set[int]:
    """Return the episodes that mention a media record."""
    return set(
        db.scalars(
            select(Mention.episode_id).where(Mention.media_id == media_id).distinct(),
        ),
    )


def authorize_media_summary_text(*, db: Session, media_id: int) -> None:
    """Lock a media record and every episode its summary would quote.

    Locks follow the shared order (the media row, then podcast rows). The
    episodes that mention the record are read again after their podcasts are
    locked; a mention committed in another podcast while this waited adds an
    episode, whose podcast is then locked too, until the set is stable. If it
    keeps changing the call refuses with ``RetryableLockError`` and releases
    its locks. The source is read afterwards through
    ``summary_source_mentions``, so no takedown of what it quotes can commit
    until the provider call and its transaction are over.

    Args:
        db: Session of the transaction that will call the provider.
        media_id: Media record about to be summarised.

    Raises:
        RetryableLockError: If the mentioning episodes kept changing.
    """
    with _released_on_refusal(db):
        lock_media_rows(session=db, media_ids=[media_id])
        episode_ids = mentioning_episodes(db, media_id)
        for _attempt in range(_STABLE_READ_ATTEMPTS):
            lock_episode_text(db=db, episode_ids=episode_ids)
            current = mentioning_episodes(db, media_id)
            if current <= episode_ids:
                return
            episode_ids |= current
        raise RetryableLockError(
            f"Mentions of media {media_id} kept changing while their episodes"
            " were being locked; retry",
        )


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


def suppression_version(db: Session) -> int:
    """Return the suppression change counter; it only ever increases.

    Caches of user-facing aggregates key on it, so the first read after a
    takedown, merge, split or backfill is committed misses the cache in
    every process.

    Args:
        db: Database session.

    Returns:
        The current counter value, ``0`` before the first change.
    """
    version: int | None = db.scalar(
        select(ContentSuppressionVersion.version).where(
            ContentSuppressionVersion.id == 1,
        ),
    )
    return int(version or 0)


def delete_mentions(db: Session, mentions: Sequence[Mention]) -> list[int]:
    """Delete mentions and every reference that would outlive them.

    Mention candidates and graph triples lose their link to the mention,
    and the ids leave every undelivered alert event, so a later mention that
    reuses an id (SQLite can) is never reported as one of them. Events keep
    their remaining ids; one left with none stays undelivered.

    Args:
        db: Session of the transaction removing the mentions.
        mentions: Rows to delete.

    Returns:
        The deleted mention ids.
    """
    mention_ids = sorted({mention.id for mention in mentions})
    if not mention_ids:
        return []
    for candidate in db.query(MentionCandidate).filter(
        MentionCandidate.mention_id.in_(mention_ids),
    ):
        candidate.mention_id = None
    for triple in db.query(GraphTriple).filter(
        GraphTriple.provenance_mention_id.in_(mention_ids),
    ):
        triple.provenance_mention_id = None
    removed = set(mention_ids)
    for event in db.query(AccountAlertEvent).filter(
        AccountAlertEvent.digest_id.is_(None),
    ):
        recorded = event.mention_ids_json
        if recorded and removed.intersection(recorded):
            event.mention_ids_json = [item for item in recorded if item not in removed]
    for mention in mentions:
        db.delete(mention)
    db.flush()
    return mention_ids


def copy_pair_suppressions(
    *,
    db: Session,
    source_media_id: int,
    created_media_id: int,
) -> int:
    """Copy a record's pair suppressions to a record split out of it.

    The new record represents (part of) the same work, so every
    ``(episode, source)`` pair taken down stays taken down as
    ``(episode, created)``. Each copy keeps its takedown case.

    Args:
        db: Session running the split.
        source_media_id: Record the new one was split from.
        created_media_id: The new record.

    Returns:
        The number of suppressions copied.
    """
    copied = 0
    for suppression, episode_id in pair_suppressions_of(db, source_media_id):
        record_suppression(
            db=db,
            takedown_request_id=suppression.takedown_request_id,
            subject_type=ContentSuppressionSubjectType.MENTION_PAIR,
            scope=ContentSuppressionScope(suppression.scope),
            subject_ids=(episode_id, created_media_id),
        )
        copied += 1
    return copied


def pair_suppressions_of(
    db: Session,
    media_id: int,
) -> list[tuple[ContentSuppression, int]]:
    """Return a media record's pair suppressions with their episode ids.

    Args:
        db: Database session.
        media_id: Media record whose ``(episode, media)`` pairs are suppressed.

    Returns:
        Each pair suppression and its episode id, in id order.
    """
    rows = (
        db.query(ContentSuppression)
        .filter(
            ContentSuppression.media_id == media_id,
            ContentSuppression.subject_type
            == ContentSuppressionSubjectType.MENTION_PAIR.value,
        )
        .order_by(ContentSuppression.id)
        .all()
    )
    # The check constraint gives every pair row an episode.
    return [(row, row.episode_id) for row in rows if row.episode_id is not None]


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
