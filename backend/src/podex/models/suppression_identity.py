"""Keep suppressions in force when an episode moves or its subject is deleted.

Suppressions are keyed by row ids (ADR 0017). The flush guard in
``podex.models.suppression_guard`` calls these steps before a flush writes:

- an ``Episode`` assigned to another podcast has its row locked and its
  persisted podcast read (``lock_moved_episodes``); if it really moves, it
  gets an episode suppression for each podcast suppression of the podcast it
  leaves, recorded or pending in the session, in the same flush and with the
  same takedown case (``carry_podcast_suppressions``);
- deleting an ``Episode`` or ``Podcast`` that a suppression covers is refused
  (``refuse_covered_deletions``);
- a move into or out of a suppressed podcast bumps the change counter at
  commit, after every other lock (``bump_after_coverage_moves``).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session, aliased

from podex.models.content_suppression import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    ContentSuppressionVersion,
    batches,
    suppression_key,
)
from podex.models.episode import Episode
from podex.models.podcast import Podcast

_COVERAGE_CHANGED = "podex_suppression_coverage_changed"
_LEFT_PODCASTS = "podex_suppression_left_podcasts"


def _podcast_changed(row: Episode) -> bool:
    """Return whether an episode's podcast column or relationship changed."""
    attributes = inspect(row).attrs
    return any(
        attributes[name].history.has_changes() for name in ("podcast_id", "podcast")
    )


@dataclass(frozen=True, slots=True)
class IdentityChanges:
    """Episodes this flush assigns to a podcast, and rows it deletes.

    ``assigned`` maps each episode whose podcast changes in memory to the
    podcast id, or new ``Podcast``, it is assigned to. Whether it really
    moves, and from where, is decided from the database under the episode's
    lock (``lock_moved_episodes``), never from the session's copy.
    """

    assigned: dict[int, object]
    deleted_episodes: tuple[int, ...]
    deleted_podcasts: tuple[int, ...]


def _destination(episode: Episode) -> object:
    """Return the podcast id, or new ``Podcast``, an episode is assigned to."""
    added = inspect(episode).attrs["podcast"].history.added
    if added:
        target = added[0]
        return target.id if target is not None and target.id is not None else target
    return episode.podcast_id


def _persisted_id(row: object) -> int:
    """Return a persistent row's id from its identity key, loading nothing.

    Reading ``row.id`` would load every row a commit expired. Dirty and
    deleted rows are persistent, so they always have an identity.
    """
    return int((inspect(row).identity or (0,))[0])


def identity_changes(session: Session) -> IdentityChanges:
    """Collect the episode podcast assignments and the episode and podcast deletes.

    Found in memory: a flush that assigns and deletes nothing runs no
    statement for them.
    """
    assigned: dict[int, object] = {}
    for row in session.dirty:
        if isinstance(row, Episode) and _podcast_changed(row):
            assigned[_persisted_id(row)] = _destination(row)
    deleted = list(session.deleted)
    return IdentityChanges(
        assigned=assigned,
        deleted_episodes=tuple(
            sorted(_persisted_id(row) for row in deleted if isinstance(row, Episode)),
        ),
        deleted_podcasts=tuple(
            sorted(_persisted_id(row) for row in deleted if isinstance(row, Podcast)),
        ),
    )


def hold_sqlite_write_lock(session: Session) -> None:
    """Take SQLite's database write lock for the rest of the transaction.

    SQLite ignores row locks. A write statement takes its single write lock,
    which a takedown also needs, so holding it from a read until the
    transaction ends gives SQLite the same order PostgreSQL gets from rows.
    The statement changes no row.

    Args:
        session: Session whose transaction takes the lock.
    """
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("UPDATE content_suppressions SET id = id WHERE 0 = 1"))


def lock_moved_episodes(
    *, session: Session, episode_ids: Iterable[int]
) -> dict[int, int]:
    """Lock the rows of episodes about to move and read their persisted podcast.

    ``FOR NO KEY UPDATE``, ascending id: the lock the move's own ``UPDATE``
    takes, taken before anything is read. Two moves of one episode therefore
    run one after the other, and the podcast read here is the one the episode
    really leaves, whatever the session's copy says. On SQLite the database
    write lock is taken first.

    The lock never conflicts with the ``FOR KEY SHARE`` that a takedown or a
    guarded flush takes on an episode row (foreign key checks), so neither
    ever waits for it.

    Args:
        session: Session whose transaction moves the episodes.
        episode_ids: Episodes whose podcast the flush changes.

    Returns:
        Each episode's persisted podcast id.
    """
    wanted = set(episode_ids)
    if not wanted:
        return {}
    hold_sqlite_write_lock(session)
    persisted: dict[int, int] = {}
    for batch in batches(wanted):
        rows = session.execute(
            select(Episode.id, Episode.podcast_id)
            .where(Episode.id.in_(batch))
            .order_by(Episode.id)
            .with_for_update(key_share=True),
        )
        persisted.update((int(row.id), int(row.podcast_id)) for row in rows)
    return persisted


def _deleted_label(kind: str, row_id: int) -> str:
    return f"{kind} {row_id} (deleted while a takedown suppression covers it)"


def refuse_covered_deletions(session: Session, changes: IdentityChanges) -> None:
    """Refuse deleting an episode or podcast that a suppression covers.

    An episode is covered by a suppression of itself, of one of its mention
    pairs, or of its podcast; a podcast by its own suppression or one of any
    of its episodes. Each batch runs one statement.

    Raises:
        ContentSuppressedError: Naming the first covered row.
    """
    for batch in batches(changes.deleted_episodes):
        covered = session.execute(
            select(Episode.id, ContentSuppression.scope)
            .join(
                ContentSuppression,
                or_(
                    ContentSuppression.episode_id == Episode.id,
                    ContentSuppression.podcast_id == Episode.podcast_id,
                ),
            )
            .where(Episode.id.in_(batch))
            .order_by(Episode.id, ContentSuppression.id)
            .limit(1),
        ).first()
        if covered is not None:
            raise ContentSuppressedError(
                scope=ContentSuppressionScope(covered.scope),
                episode_id=covered.id,
                row=_deleted_label("Episode", covered.id),
            )
    podcast_episode = aliased(Episode, name="deleted_podcast_episode")
    for batch in batches(changes.deleted_podcasts):
        covered = session.execute(
            select(
                Podcast.id,
                ContentSuppression.scope,
                ContentSuppression.episode_id,
            )
            .outerjoin(podcast_episode, podcast_episode.podcast_id == Podcast.id)
            .join(
                ContentSuppression,
                or_(
                    ContentSuppression.podcast_id == Podcast.id,
                    ContentSuppression.episode_id == podcast_episode.id,
                ),
            )
            .where(Podcast.id.in_(batch))
            .order_by(Podcast.id, ContentSuppression.id)
            .limit(1),
        ).first()
        if covered is not None:
            raise ContentSuppressedError(
                scope=ContentSuppressionScope(covered.scope),
                episode_id=covered.episode_id,
                podcast_id=covered.id,
                row=_deleted_label("Podcast", covered.id),
            )


def _podcast_suppressions(
    session: Session,
    podcast_ids: set[int],
) -> dict[int, list[ContentSuppression]]:
    """Return the podcast suppressions of ``podcast_ids``, recorded or pending.

    A suppression added in this session but not yet written (a takedown that
    records it in the same flush as the move) counts as much as a stored one.
    """
    found: dict[int, list[ContentSuppression]] = defaultdict(list)
    pending = [
        row
        for row in session.new
        if isinstance(row, ContentSuppression)
        and row.subject_type == ContentSuppressionSubjectType.PODCAST.value
        and row.podcast_id in podcast_ids
    ]
    for batch in batches(podcast_ids):
        stored = session.scalars(
            select(ContentSuppression)
            .where(
                ContentSuppression.subject_type
                == ContentSuppressionSubjectType.PODCAST.value,
                ContentSuppression.podcast_id.in_(batch),
            )
            .order_by(ContentSuppression.id),
        )
        for suppression in [*stored]:
            if suppression.podcast_id is not None:
                found[suppression.podcast_id].append(suppression)
    for suppression in pending:
        if suppression.podcast_id is not None:
            found[suppression.podcast_id].append(suppression)
    return found


def carry_podcast_suppressions(
    session: Session,
    moved: dict[int, int],
    destinations: dict[int, object],
) -> None:
    """Give each moved episode the podcast suppressions of the podcast it leaves.

    ``moved`` maps each episode to its persisted podcast, read under the
    episode's lock. A podcast suppression covers the podcast's episodes
    through their current ``podcast_id``, so a moved episode would otherwise
    leave it behind. Each one becomes an episode suppression with the same
    scope and takedown case, added to this flush; a key already recorded is
    not added again.

    Episodes that left a podcast earlier in this transaction also get any
    podcast suppression this flush records for it. A takedown that read its
    episodes before the move was written, and records one scope per flush,
    therefore covers the episode with every scope it reports.

    A move into or out of a suppressed podcast changes what is published, so
    it marks the transaction for a change-counter bump at commit. Runs one
    statement per batch of podcasts, and one per batch of keys when there is
    something to carry; none when nothing moves and no suppression of a
    podcast left earlier is pending.
    """
    left: dict[int, set[int]] = session.info.setdefault(_LEFT_PODCASTS, {})
    wanted: dict[str, tuple[int, ContentSuppression]] = {}
    for suppression in session.new:
        if (
            isinstance(suppression, ContentSuppression)
            and suppression.subject_type == ContentSuppressionSubjectType.PODCAST.value
            and suppression.podcast_id in left
        ):
            for episode_id in sorted(left[suppression.podcast_id]):
                _want(wanted, episode_id, suppression)
    if moved:
        joined = {
            destination
            for destination in destinations.values()
            if isinstance(destination, int)
        }
        by_podcast = _podcast_suppressions(session, set(moved.values()) | joined)
        if by_podcast:
            session.info[_COVERAGE_CHANGED] = True
        for episode_id, podcast_id in sorted(moved.items()):
            left.setdefault(podcast_id, set()).add(episode_id)
            for suppression in by_podcast.get(podcast_id, []):
                _want(wanted, episode_id, suppression)
    if wanted:
        _add_missing_episode_suppressions(session, wanted)


def _want(
    wanted: dict[str, tuple[int, ContentSuppression]],
    episode_id: int,
    suppression: ContentSuppression,
) -> None:
    """Record that ``episode_id`` needs an episode copy of ``suppression``."""
    key = suppression_key(
        subject_type=ContentSuppressionSubjectType.EPISODE,
        scope=ContentSuppressionScope(suppression.scope),
        subject_ids=(episode_id,),
    )
    wanted.setdefault(key, (episode_id, suppression))


def _add_missing_episode_suppressions(
    session: Session,
    wanted: dict[str, tuple[int, ContentSuppression]],
) -> None:
    """Add each wanted episode suppression whose key is not recorded yet."""
    recorded = {
        row.suppression_key
        for row in session.new
        if isinstance(row, ContentSuppression)
    }
    for batch in batches(wanted):
        recorded.update(
            session.scalars(
                select(ContentSuppression.suppression_key).where(
                    ContentSuppression.suppression_key.in_(batch),
                ),
            ),
        )
    for key, (episode_id, suppression) in sorted(wanted.items()):
        if key not in recorded:
            session.add(
                ContentSuppression(
                    suppression_key=key,
                    takedown_request_id=suppression.takedown_request_id,
                    subject_type=ContentSuppressionSubjectType.EPISODE.value,
                    scope=suppression.scope,
                    episode_id=episode_id,
                ),
            )


def bump_suppression_version(session: Session) -> None:
    """Increase the suppression change counter in this transaction.

    The row is the last lock a suppression-relevant write takes, after media,
    episode and podcast rows, so it adds no new lock order. The row is seeded
    when the table is created, so this only updates it and concurrent bumps
    queue on its row lock.

    Args:
        session: Session of the transaction that changed suppressed content.

    Raises:
        LookupError: If the counter row is missing (migrations not applied).
    """
    row = session.get(ContentSuppressionVersion, 1, with_for_update=True)
    if row is None:
        message = "content_suppression_versions has no row; apply the migrations"
        raise LookupError(message)
    row.version += 1
    session.flush()


def bump_after_coverage_moves(session: Session) -> None:
    """Bump the change counter at commit when a move changed coverage.

    Pending writes are flushed first, so every lock they take comes before
    the counter, which stays the last lock of the transaction.
    """
    session.flush()
    if session.info.pop(_COVERAGE_CHANGED, False):
        bump_suppression_version(session)


def forget_moves(session: Session) -> None:
    """Drop what this module remembers once the whole transaction ends."""
    session.info.pop(_COVERAGE_CHANGED, None)
    session.info.pop(_LEFT_PODCASTS, None)
