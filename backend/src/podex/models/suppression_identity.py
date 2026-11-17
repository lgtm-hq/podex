"""Refuse moves and deletes that would take a row out of a suppression.

Suppressions are keyed by row ids (ADR 0017). The flush guard in
``podex.models.suppression_guard`` calls these steps before a flush writes:

- a change of an ``Episode``'s podcast is refused when the podcast it leaves
  or the one it joins has a podcast suppression, stored or pending in the
  session, and refused as stale when the database no longer has the episode
  in the podcast the session remembers (``refuse_moves``);
- deleting an ``Episode`` or ``Podcast`` that a suppression covers, stored or
  pending in the session, is refused (``refuse_covered_deletions``).

An episode's own episode and pair suppressions are keyed by its id and follow
it, so they do not block a move between two podcasts without suppressions.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import inspect, or_, select, text
from sqlalchemy.orm import Session, aliased

from podex.models.content_suppression import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    RetryableLockError,
    batches,
)
from podex.models.episode import Episode
from podex.models.podcast import Podcast


@dataclass(frozen=True, slots=True)
class Move:
    """An episode the flush assigns to another podcast.

    ``origin`` is the podcast the session remembers; ``destination`` is the
    podcast id it is assigned to, or ``None`` for a podcast the same flush
    creates (which has no suppression yet).
    """

    origin: int
    destination: int | None

    @property
    def podcasts(self) -> set[int]:
        """The existing podcasts the move names."""
        joined = {self.destination} if self.destination is not None else set()
        return {self.origin} | joined

    @property
    def destination_label(self) -> str:
        """Name the podcast the move joins, for an error message."""
        if self.destination is None:
            return "a new podcast"
        return f"podcast {self.destination}"


@dataclass(frozen=True, slots=True)
class IdentityChanges:
    """Episodes this flush moves, and the episodes and podcasts it deletes."""

    moves: dict[int, Move]
    deleted_episodes: tuple[int, ...]
    deleted_podcasts: tuple[int, ...]


def _persisted_id(row: object) -> int:
    """Return a persistent row's id from its identity key, loading nothing.

    Reading ``row.id`` would load every row a commit expired. Dirty and
    deleted rows are persistent, so they always have an identity.
    """
    return int((inspect(row).identity or (0,))[0])


def _move_of(episode: Episode) -> Move | None:
    """Return the move an episode's pending podcast change makes, if any.

    Whether there is a move is read from attribute history alone, so a change
    of other columns loads nothing, even when ``podcast_id`` is deferred or
    expired. The column keeps active history (``suppression_guard``), so its
    replaced value is known even when a commit expired the row; a
    relationship assignment reaches the column only during the flush, which
    then still holds the remembered podcast.
    """
    state = inspect(episode)
    column = state.attrs["podcast_id"].history
    added = state.attrs["podcast"].history.added
    if added and added[0] is not None:
        destination = added[0].id
    elif column.added:
        destination = column.added[0]
    else:
        # Re-assigning the podcast an episode already has leaves no history.
        return None
    origin = column.deleted[0] if column.deleted else episode.podcast_id
    return Move(origin=int(origin), destination=destination)


def identity_changes(session: Session) -> IdentityChanges:
    """Collect the episode moves and the episode and podcast deletes.

    Found in memory: a flush that moves and deletes nothing runs no statement
    for them.
    """
    moves: dict[int, Move] = {}
    for row in session.dirty:
        if isinstance(row, Episode) and (move := _move_of(row)) is not None:
            moves[_persisted_id(row)] = move
    deleted = list(session.deleted)
    return IdentityChanges(
        moves=moves,
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


def lock_podcast_rows(*, session: Session, podcast_ids: Iterable[int]) -> None:
    """Lock podcast rows by id ``FOR SHARE``, ascending id.

    The rows a flush deletes, and those its moves leave and join. A takedown
    takes the row ``FOR UPDATE``, so it waits for the flush, or the flush
    waits for it and then reads its suppression and refuses.

    Args:
        session: Session whose transaction deletes or moves.
        podcast_ids: Deleted podcasts, and the moves' remembered origins and
            destinations.
    """
    for batch in batches(podcast_ids):
        session.execute(
            select(Podcast.id)
            .where(Podcast.id.in_(batch))
            .order_by(Podcast.id)
            .with_for_update(read=True),
        ).all()


def lock_moved_episodes(
    *,
    session: Session,
    episode_ids: Iterable[int],
) -> dict[int, int]:
    """Lock the rows of episodes about to move and read their persisted podcast.

    ``FOR NO KEY UPDATE``, ascending id, after the podcast rows: the lock the
    move's own ``UPDATE`` takes, taken before the podcast is read, so two
    moves of one episode run one after the other. On SQLite the database
    write lock is taken first.

    Args:
        session: Session whose transaction moves the episodes.
        episode_ids: Episodes whose podcast the flush changes.

    Returns:
        Each episode's persisted podcast id.
    """
    hold_sqlite_write_lock(session)
    persisted: dict[int, int] = {}
    for batch in batches(episode_ids):
        rows = session.execute(
            select(Episode.id, Episode.podcast_id)
            .where(Episode.id.in_(batch))
            .order_by(Episode.id)
            .with_for_update(key_share=True),
        )
        persisted.update((int(row.id), int(row.podcast_id)) for row in rows)
    return persisted


def _pending(session: Session) -> list[ContentSuppression]:
    """Suppressions added in this session and not yet written."""
    return [row for row in session.new if isinstance(row, ContentSuppression)]


def _suppressed_podcasts(
    session: Session,
    podcast_ids: set[int],
) -> dict[int, ContentSuppressionScope]:
    """Return which podcasts have a podcast suppression, stored or pending.

    Runs one statement per batch of podcasts.
    """
    found: dict[int, ContentSuppressionScope] = {}
    for batch in batches(podcast_ids):
        found.update(
            (int(podcast_id), ContentSuppressionScope(scope))
            for podcast_id, scope in session.execute(
                select(ContentSuppression.podcast_id, ContentSuppression.scope)
                .where(
                    ContentSuppression.subject_type
                    == ContentSuppressionSubjectType.PODCAST.value,
                    ContentSuppression.podcast_id.in_(batch),
                )
                .order_by(ContentSuppression.id),
            )
            if podcast_id is not None
        )
    for row in _pending(session):
        if (
            row.subject_type == ContentSuppressionSubjectType.PODCAST.value
            and row.podcast_id in podcast_ids
        ):
            found.setdefault(row.podcast_id, ContentSuppressionScope(row.scope))
    return found


def refuse_moves(session: Session, moves: dict[int, Move]) -> None:
    """Refuse a stale move, and a move that leaves or joins a suppressed podcast.

    The caller has locked the podcasts the moves name ``FOR SHARE``
    (``lock_podcast_rows``). This locks the episode rows, reading where each
    episode really is; if that is not the podcast the session remembers, the
    move is refused as stale instead of locking the podcast found that way.

    Raises:
        RetryableLockError: If an episode was moved since it was loaded.
        ContentSuppressedError: If a podcast the move names is suppressed.
    """
    if not moves:
        return
    podcasts = set().union(*(move.podcasts for move in moves.values()))
    persisted = lock_moved_episodes(session=session, episode_ids=moves)
    for episode_id, move in sorted(moves.items()):
        if persisted.get(episode_id) != move.origin:
            message = (
                f"Episode {episode_id} is no longer in podcast {move.origin}:"
                " another change moved it since this session loaded it. Roll"
                " back, reload the episode and retry the move"
            )
            raise RetryableLockError(message)
    suppressed = _suppressed_podcasts(session, podcasts)
    for episode_id, move in sorted(moves.items()):
        for podcast_id in sorted(move.podcasts & set(suppressed)):
            raise ContentSuppressedError(
                scope=suppressed[podcast_id],
                episode_id=episode_id,
                podcast_id=podcast_id,
                row=(
                    f"Episode {episode_id} (moved from podcast {move.origin} to"
                    f" {move.destination_label})"
                ),
            )


def _deleted_label(kind: str, row_id: int) -> str:
    return f"{kind} {row_id} (deleted while a takedown suppression covers it)"


def _refuse_stored_coverage(session: Session, changes: IdentityChanges) -> None:
    """Refuse a delete a stored suppression covers; one statement per batch."""
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


def _refuse_pending_coverage(session: Session, changes: IdentityChanges) -> None:
    """Refuse a delete a suppression pending in this session covers.

    Runs statements only when the flush both deletes and records
    suppressions: one for the deleted episodes' podcasts and one for the
    deleted podcasts' episodes, per batch.
    """
    pending = _pending(session)
    if not pending or not (changes.deleted_episodes or changes.deleted_podcasts):
        return
    podcast_of: dict[int, int] = {}
    for batch in batches(changes.deleted_episodes):
        podcast_of.update(
            (int(row.id), int(row.podcast_id))
            for row in session.execute(
                select(Episode.id, Episode.podcast_id).where(Episode.id.in_(batch)),
            )
        )
    episodes_of: dict[int, set[int]] = {}
    for batch in batches(changes.deleted_podcasts):
        for row in session.execute(
            select(Episode.id, Episode.podcast_id).where(
                Episode.podcast_id.in_(batch),
            ),
        ):
            episodes_of.setdefault(int(row.podcast_id), set()).add(int(row.id))
    for suppression in sorted(pending, key=lambda row: row.suppression_key):
        scope = ContentSuppressionScope(suppression.scope)
        for episode_id in sorted(podcast_of):
            if (
                suppression.episode_id == episode_id
                or suppression.podcast_id == podcast_of[episode_id]
            ):
                raise ContentSuppressedError(
                    scope=scope,
                    episode_id=episode_id,
                    row=_deleted_label("Episode", episode_id),
                )
        for podcast_id in changes.deleted_podcasts:
            if suppression.podcast_id == podcast_id or (
                suppression.episode_id in episodes_of.get(podcast_id, set())
            ):
                raise ContentSuppressedError(
                    scope=scope,
                    episode_id=suppression.episode_id,
                    podcast_id=podcast_id,
                    row=_deleted_label("Podcast", podcast_id),
                )


def refuse_covered_deletions(session: Session, changes: IdentityChanges) -> None:
    """Refuse deleting an episode or podcast that a suppression covers.

    An episode is covered by a suppression of itself, of one of its mention
    pairs, or of its podcast; a podcast by its own suppression or one of any
    of its episodes. Suppressions stored and pending in the session count.

    Raises:
        ContentSuppressedError: Naming the first covered row.
    """
    _refuse_stored_coverage(session, changes)
    _refuse_pending_coverage(session, changes)
