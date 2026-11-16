"""Refuse ORM writes that would recreate suppressed content.

Session events on every :class:`~sqlalchemy.orm.Session` check each flush, so
a writer cannot skip the check by calling a lower-level function or by being
written after this module. Importing ``podex.models`` installs the events.

Guarded writes (``GUARDED_MODELS``):

- a ``Mention`` inserted, or moved to another episode or media, is checked
  against the ``mentions`` scope for its episode and its ``(episode, media)``
  pair;
- a ``SemanticChunk``, ``EpisodeSummary`` or ``DerivativeGenerationRun``
  inserted or changed, a ``GraphTriple`` or ``MediaRelation`` inserted with a
  provenance episode, and a ``Transcript`` whose cleaned or digest text is set,
  are checked against the ``derivatives`` scope for their episode.

Ordering with a concurrent takedown, which locks the podcast rows it covers
``FOR UPDATE`` before reading what to delete:

- before the flush writes, the podcast rows of the episodes it touches are
  locked ``FOR SHARE`` (ascending id). On PostgreSQL a takedown that holds the
  lock makes the writer wait; a writer that holds it makes the takedown wait
  until the writer commits, after which the takedown deletes what was written.
  Locking before writing means a writer never holds a row the takedown is
  about to delete while it waits for the podcast lock;
- after the flush writes, the suppression check runs. On SQLite, which ignores
  ``FOR SHARE`` and has one writer, the writer holds the database write lock by
  then, so a takedown committed while the writer waited for that lock is seen
  and no takedown can commit until the writer ends.

A check that passed is remembered until the transaction ends: the locks above
keep its answer valid until then.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, SessionTransaction, UOWTransaction

from podex.models.content_suppression import (
    ContentSuppressedError,
    ContentSuppressionScope,
    suppressed_clause,
)
from podex.models.derivative_generation_run import DerivativeGenerationRun
from podex.models.episode import Episode
from podex.models.episode_summary import EpisodeSummary
from podex.models.graph_triple import GraphTriple
from podex.models.media_relation import MediaRelation
from podex.models.mention import Mention
from podex.models.podcast import Podcast
from podex.models.semantic_chunk import SemanticChunk
from podex.models.transcript import Transcript

GUARDED_MODELS: tuple[type[Any], ...] = (
    Mention,
    SemanticChunk,
    EpisodeSummary,
    DerivativeGenerationRun,
    GraphTriple,
    MediaRelation,
    Transcript,
)
"""Models whose writes are checked against content suppressions."""

_EPISODE_DERIVATIVES = (SemanticChunk, EpisodeSummary, DerivativeGenerationRun)
_DERIVED_TRANSCRIPT_TEXT = ("cleaned_text", "digest_text")
_LOCKED_PODCASTS = "podex_suppression_locked_podcasts"
_CHECKED = "podex_suppression_checked"


@dataclass(frozen=True, slots=True)
class _Requirement:
    """One scope a pending write must not be suppressed under."""

    scope: ContentSuppressionScope
    episode_id: int
    media_id: int | None = None


def _changed(instance: object, *attributes: str) -> bool:
    """Return whether any named attribute of a pending row has changed."""
    state = inspect(instance)
    return any(state.attrs[name].history.has_changes() for name in attributes)


def _episode_id(instance: object, attribute: str, relationship: str) -> int | None:
    """Return a row's episode id, from its column or its related episode."""
    value: int | None = getattr(instance, attribute)
    if value is None:
        related: Episode | None = getattr(instance, relationship, None)
        value = related.id if related is not None else None
    return value


def _media_id(mention: Mention) -> int | None:
    """Return a mention's media id, from its column or its related media."""
    value: int | None = mention.media_id
    if value is None and mention.media is not None:
        value = mention.media.id
    return value


def _requirements(session: Session) -> Iterator[_Requirement]:
    """Yield the suppression checks the pending inserts and updates need."""
    for instance in session.new:
        yield from _row_requirements(instance, inserted=True)
    for instance in session.dirty:
        if session.is_modified(instance):
            yield from _row_requirements(instance, inserted=False)


def _row_requirements(instance: object, *, inserted: bool) -> Iterator[_Requirement]:
    """Yield the checks one pending row needs; unguarded rows need none."""
    if isinstance(instance, Mention):
        if not inserted and not _changed(
            instance,
            "episode_id",
            "media_id",
            "episode",
            "media",
        ):
            return
        episode_id = _episode_id(instance, "episode_id", "episode")
        if episode_id is not None:
            yield _Requirement(
                scope=ContentSuppressionScope.MENTIONS,
                episode_id=episode_id,
                media_id=_media_id(instance),
            )
        return
    episode_id = _derived_episode_id(instance, inserted=inserted)
    if episode_id is not None:
        yield _Requirement(
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_id=episode_id,
        )


def _derived_episode_id(instance: object, *, inserted: bool) -> int | None:
    """Return the episode a pending derived row belongs to, if it is guarded."""
    if isinstance(instance, _EPISODE_DERIVATIVES):
        return _episode_id(instance, "episode_id", "episode")
    if isinstance(instance, (GraphTriple, MediaRelation)):
        return instance.provenance_episode_id if inserted else None
    if isinstance(instance, Transcript) and any(
        getattr(instance, name) is not None for name in _DERIVED_TRANSCRIPT_TEXT
    ):
        if inserted or _changed(instance, *_DERIVED_TRANSCRIPT_TEXT):
            return _episode_id(instance, "episode_id", "episode")
    return None


def _transaction_cache(session: Session, key: str) -> set[Any]:
    """Return a per-transaction set stored on the session."""
    cache: set[Any] = session.info.setdefault(key, set())
    return cache


def lock_podcasts_of_episodes(
    *,
    session: Session,
    episode_ids: Iterable[int],
) -> None:
    """Lock the podcast rows of ``episode_ids`` ``FOR SHARE``, ascending id.

    A takedown locks the same rows ``FOR UPDATE``, so a writer and a takedown
    covering the same podcast run one after the other. SQLite ignores the
    clause. Rows already locked in this transaction are skipped.

    Args:
        session: Session whose transaction takes the locks.
        episode_ids: Episodes about to receive guarded writes.
    """
    locked = _transaction_cache(session, _LOCKED_PODCASTS)
    pending = set(episode_ids) - {episode for _, episode in locked}
    if not pending:
        return
    rows = session.execute(
        select(Podcast.id, Episode.id)
        .join(Episode, Episode.podcast_id == Podcast.id)
        .where(Episode.id.in_(sorted(pending)))
        .order_by(Podcast.id)
        .with_for_update(read=True, of=Podcast),
    ).all()
    locked.update((podcast_id, episode_id) for podcast_id, episode_id in rows)


def check_requirement(*, session: Session, requirement: _Requirement) -> None:
    """Raise when ``requirement``'s content is suppressed.

    Raises:
        ContentSuppressedError: If an approved takedown covers the write.
    """
    checked = _transaction_cache(session, _CHECKED)
    if requirement in checked:
        return
    suppressed = session.execute(
        select(
            suppressed_clause(
                scope=requirement.scope,
                episode_id=requirement.episode_id,
                media_id=requirement.media_id,
            ),
        ),
    ).scalar_one()
    if suppressed:
        raise ContentSuppressedError(
            scope=requirement.scope,
            episode_id=requirement.episode_id,
            media_id=requirement.media_id,
        )
    checked.add(requirement)


@event.listens_for(Session, "before_flush")
def lock_before_guarded_writes(
    session: Session,
    _flush_context: UOWTransaction,
    _instances: object,
) -> None:
    """Take the podcast locks before the flush writes any guarded row."""
    episode_ids = {requirement.episode_id for requirement in _requirements(session)}
    if episode_ids:
        lock_podcasts_of_episodes(session=session, episode_ids=episode_ids)


@event.listens_for(Session, "after_flush")
def refuse_suppressed_writes(
    session: Session,
    _flush_context: UOWTransaction,
) -> None:
    """Check each guarded row the flush wrote; raising rolls the flush back."""
    requirements = set(_requirements(session))
    if not requirements:
        return
    lock_podcasts_of_episodes(
        session=session,
        episode_ids={requirement.episode_id for requirement in requirements},
    )
    for requirement in sorted(
        requirements,
        key=lambda item: (item.scope.value, item.episode_id, item.media_id or 0),
    ):
        check_requirement(session=session, requirement=requirement)


@event.listens_for(Session, "after_transaction_end")
def _forget_transaction_checks(
    session: Session,
    transaction: SessionTransaction,
) -> None:
    """Drop cached locks and checks when the transaction or a savepoint ends.

    PostgreSQL releases locks taken inside a savepoint that rolls back, so a
    savepoint's end forgets them too. A flush's internal subtransaction is
    neither, and keeps them.
    """
    if transaction.parent is None or transaction.nested:
        session.info.pop(_LOCKED_PODCASTS, None)
        session.info.pop(_CHECKED, None)
