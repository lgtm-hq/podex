"""Refuse ORM writes that would recreate, rewrite or move suppressed content.

Session events on every :class:`~sqlalchemy.orm.Session` check each flush, so
a writer cannot skip the check by calling a lower-level function or by being
written after this module. Importing ``podex.models`` installs the events.
Writes that bypass the ORM flush (Core statements, raw SQL, ``bulk_*``) are
not seen here; ``tests/test_guarded_table_writes.py`` forbids them for these
tables.

Guarded writes (``GUARDED_MODELS``, one rule per model in ``_RULES``):

- ``Mention``: an insert, and a move to another episode or media record, is
  checked against the ``mentions`` scope at its destination and, for a move,
  at its origin, so a hidden mention can neither appear nor be carried out of
  its suppressed pair;
- ``SemanticChunk``, ``EpisodeSummary``, ``DerivativeGenerationRun``: any
  insert or change, at the row's episode and, when it moves, its old one;
- ``GraphTriple``, ``MediaRelation``: an insert with a provenance episode,
  and a change to evidence, content or provenance, unless the change only
  removes them. Repointing the media records and the key (a media merge) is
  identity maintenance and allowed;
- ``Transcript``: an insert, text change or episode change while it carries
  cleaned or digest text.

Lock order, the same as the media merge (media rows before podcast rows):

1. media rows of guarded mentions, ``FOR KEY SHARE``, ascending id;
2. podcast rows of every episode touched, ``FOR SHARE``, ascending id within
   each statement of up to ``SUPPRESSION_BATCH_SIZE`` episodes.

The podcast order across statements cannot form a deadlock. Share locks do
not conflict with each other. The locks that do conflict with them (a
takedown's ``FOR UPDATE``, and an operator's update or archive) each take a
single podcast row, so the other transaction never waits for a second
podcast while it holds one.

Both are taken before the flush writes, so a writer never holds a row a
takedown will delete while it waits. A takedown takes the same media rows and
then its podcast rows ``FOR UPDATE``. On PostgreSQL a writer and a takedown of
the same podcast therefore run one after the other. The check itself runs
after the flush writes: on SQLite, which ignores row locks and has one writer,
the writer then holds the database write lock, so a takedown that committed
while it waited for that lock is seen.

Checks are batched: each flush runs a bounded number of statements
(``SUPPRESSION_BATCH_SIZE`` ids or pairs each), independent of how many rows
it writes. A check that passed is remembered until the transaction or
savepoint ends, and forgotten as soon as this session writes a suppression
row, so a takedown executed in the same transaction applies to later writes.
"""

from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import (
    ORMExecuteState,
    Session,
    SessionTransaction,
    UOWTransaction,
)

from podex.models.content_suppression import (
    SUPPRESSION_BATCH_SIZE,
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    suppressed_episodes_statement,
    suppressed_pairs_statement,
)
from podex.models.derivative_generation_run import DerivativeGenerationRun
from podex.models.episode import Episode
from podex.models.episode_summary import EpisodeSummary
from podex.models.graph_triple import GraphTriple
from podex.models.media import Media
from podex.models.media_relation import MediaRelation
from podex.models.mention import Mention
from podex.models.podcast import Podcast
from podex.models.semantic_chunk import SemanticChunk
from podex.models.transcript import Transcript

_LOCKED_EPISODES = "podex_suppression_locked_episodes"
_LOCKED_MEDIA = "podex_suppression_locked_media"
_CHECKED = "podex_suppression_checked"
_ORIGINS = "podex_suppression_origins"

_DERIVED_TRANSCRIPT_TEXT = ("cleaned_text", "digest_text")
_TRIPLE_CONTENT = (
    "evidence_text",
    "object_value",
    "predicate",
    "confidence",
    "metadata_json",
    "provenance_episode_id",
)
_RELATION_CONTENT = (
    "evidence_text",
    "relation_type",
    "confidence",
    "metadata_json",
    "provenance_episode_id",
)


@dataclass(frozen=True, slots=True)
class _Requirement:
    """One scope a pending write must not be suppressed under.

    ``row`` names the write for the error message and takes no part in
    equality, so writes sharing a check are looked up once.
    """

    scope: ContentSuppressionScope
    episode_id: int
    media_id: int | None = None
    row: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class _Pending:
    """A guarded row in the flush and whether it is new."""

    row: Any
    inserted: bool


_Rule = Callable[[_Pending], Iterator[_Requirement]]


def _label(pending: "_Pending", why: str) -> str:
    """Name a refused row and the reason, for the error an operator sees."""
    row_id = getattr(pending.row, "id", None)
    name = type(pending.row).__name__
    return f"{name} {row_id} ({why})" if row_id else f"new {name} ({why})"


def _changed(row: object, *attributes: str) -> bool:
    """Return whether any named attribute of a pending row has changed."""
    state = inspect(row)
    return any(state.attrs[name].history.has_changes() for name in attributes)


def _previous(row: object, column: str) -> Any:
    """Return a column's value before this flush.

    Before the flush a relationship-only change has not reached the column,
    which then still holds the old value. A column in ``_ORIGIN_COLUMNS`` has
    active history, so its old value is recorded even when the row was expired.
    """
    history = inspect(row).attrs[column].history
    if history.deleted:
        return history.deleted[0]
    return getattr(row, column)


def _assigned_id(row: object, column: str, relationship: str) -> int | None:
    """Return the id a row points at, preferring a newly assigned relationship.

    A relationship assignment reaches its column only during the flush, while
    a column assignment leaves a loaded relationship stale; so the
    relationship counts only when it was itself assigned.
    """
    added = inspect(row).attrs[relationship].history.added
    if added and added[0] is not None and added[0].id is not None:
        assigned: int = added[0].id
        return assigned
    value: int | None = getattr(row, column)
    return value


def _mention_rule(pending: _Pending) -> Iterator[_Requirement]:
    """A new or moved mention may not land in, or leave, a suppressed pair."""
    mention: Mention = pending.row
    moved = not pending.inserted and _changed(
        mention,
        "episode_id",
        "media_id",
        "episode",
        "media",
    )
    if not pending.inserted and not moved:
        return
    episode_id = _assigned_id(mention, "episode_id", "episode")
    if episode_id is not None:
        yield _Requirement(
            scope=ContentSuppressionScope.MENTIONS,
            episode_id=episode_id,
            media_id=_assigned_id(mention, "media_id", "media"),
            row=_label(pending, "written into the pair"),
        )


def _mention_origin(pending: _Pending) -> Iterator[_Requirement]:
    """A moved mention's old pair, read before the flush changes it."""
    mention: Mention = pending.row
    if pending.inserted or not _changed(
        mention,
        "episode_id",
        "media_id",
        "episode",
        "media",
    ):
        return
    episode_id = _previous(mention, "episode_id")
    media_id = _previous(mention, "media_id")
    if episode_id is not None:
        yield _Requirement(
            scope=ContentSuppressionScope.MENTIONS,
            episode_id=episode_id,
            media_id=media_id,
            row=_label(pending, "moved out of its suppressed pair"),
        )


def _episode_derivative_rule(pending: _Pending) -> Iterator[_Requirement]:
    """Episode derivatives may not be written for a suppressed episode."""
    episode_id = _assigned_id(pending.row, "episode_id", "episode")
    if episode_id is not None:
        yield _Requirement(
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_id=episode_id,
            row=_label(pending, "written for the episode"),
        )


def _episode_derivative_origin(pending: _Pending) -> Iterator[_Requirement]:
    """An episode derivative moved out of an episode, checked at its origin."""
    if pending.inserted or not _changed(pending.row, "episode_id", "episode"):
        return
    episode_id = _previous(pending.row, "episode_id")
    if episode_id is not None:
        yield _Requirement(
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_id=episode_id,
            row=_label(pending, "moved out of the episode"),
        )


def _provenance_rule(content: tuple[str, ...]) -> _Rule:
    """Build the rule for a row whose evidence comes from a provenance episode.

    Inserting it with a provenance episode, or changing its content or
    provenance to anything but ``None``, is checked at the provenance episode.
    Changes that only remove content, and media repoints, are not.
    """

    def rule(pending: _Pending) -> Iterator[_Requirement]:
        row = pending.row
        if not pending.inserted:
            changed = [name for name in content if _changed(row, name)]
            if all(getattr(row, name) is None for name in changed):
                return
        episode_id: int | None = row.provenance_episode_id
        if episode_id is not None:
            yield _Requirement(
                scope=ContentSuppressionScope.DERIVATIVES,
                episode_id=episode_id,
                row=_label(pending, "evidence written for the provenance episode"),
            )

    return rule


def _provenance_origin() -> _Rule:
    """Build the origin check for evidence text moved off a provenance episode.

    Detaching provenance is allowed once the evidence text is gone; moving or
    detaching it while the text stays would carry the text away.
    """

    def origin(pending: _Pending) -> Iterator[_Requirement]:
        row = pending.row
        if pending.inserted or not _changed(row, "provenance_episode_id"):
            return
        if row.evidence_text is None:
            return
        episode_id = _previous(row, "provenance_episode_id")
        if episode_id is not None:
            yield _Requirement(
                scope=ContentSuppressionScope.DERIVATIVES,
                episode_id=episode_id,
                row=_label(pending, "evidence moved off the provenance episode"),
            )

    return origin


def _carries_derived_text(transcript: Transcript) -> bool:
    return any(
        getattr(transcript, name) is not None for name in _DERIVED_TRANSCRIPT_TEXT
    )


def _transcript_rule(pending: _Pending) -> Iterator[_Requirement]:
    """Derived transcript text may not be set on or moved to a suppressed episode.

    Clearing one derived field while the other keeps its text only removes
    content, so it is not checked.
    """
    transcript: Transcript = pending.row
    if not _carries_derived_text(transcript):
        return
    sets_text = any(
        _changed(transcript, name) and getattr(transcript, name) is not None
        for name in _DERIVED_TRANSCRIPT_TEXT
    )
    if pending.inserted or sets_text or _changed(transcript, "episode_id", "episode"):
        yield from _episode_derivative_rule(pending)


def _transcript_origin(pending: _Pending) -> Iterator[_Requirement]:
    """Derived transcript text moved out of an episode, checked at its origin."""
    if _carries_derived_text(pending.row):
        yield from _episode_derivative_origin(pending)


_RULES: dict[type[Any], tuple[_Rule, _Rule]] = {
    Mention: (_mention_rule, _mention_origin),
    SemanticChunk: (_episode_derivative_rule, _episode_derivative_origin),
    EpisodeSummary: (_episode_derivative_rule, _episode_derivative_origin),
    DerivativeGenerationRun: (_episode_derivative_rule, _episode_derivative_origin),
    GraphTriple: (
        _provenance_rule(_TRIPLE_CONTENT),
        _provenance_origin(),
    ),
    MediaRelation: (
        _provenance_rule(_RELATION_CONTENT),
        _provenance_origin(),
    ),
    Transcript: (_transcript_rule, _transcript_origin),
}

GUARDED_MODELS: tuple[type[Any], ...] = tuple(_RULES)
"""Models whose writes are checked against content suppressions."""

_ORIGIN_COLUMNS: dict[type[Any], tuple[str, ...]] = {
    Mention: ("episode_id", "media_id"),
    SemanticChunk: ("episode_id",),
    EpisodeSummary: ("episode_id",),
    DerivativeGenerationRun: ("episode_id",),
    GraphTriple: ("provenance_episode_id",),
    MediaRelation: ("provenance_episode_id",),
    Transcript: ("episode_id",),
}
"""Columns whose value before a change the origin checks read."""


def _load_replaced_value(*_event: object) -> None:
    """Listen to a set only so that SQLAlchemy loads the value it replaces.

    A row is expired after every commit. Assigning an expired column records
    no old value unless a listener asks for active history, and the origin
    check would then compare the destination with itself.
    """


def _pending_rows(session: Session) -> Iterator[_Pending]:
    """Yield the guarded rows this flush inserts or materially changes."""
    for row in session.new:
        if type(row) in _RULES:
            yield _Pending(row=row, inserted=True)
    for row in session.dirty:
        if type(row) in _RULES and session.is_modified(row):
            yield _Pending(row=row, inserted=False)


def _requirements(session: Session, *, origins: bool) -> set[_Requirement]:
    """Collect destination checks, or origin checks, for the pending rows."""
    side = 1 if origins else 0
    return {
        requirement
        for pending in _pending_rows(session)
        for requirement in _RULES[type(pending.row)][side](pending)
    }


def _transaction_cache(session: Session, key: str) -> set[Any]:
    """Return a per-transaction set stored on the session."""
    cache: set[Any] = session.info.setdefault(key, set())
    return cache


def _batches(values: Iterable[Any]) -> Iterator[list[Any]]:
    """Split sorted values into statements of ``SUPPRESSION_BATCH_SIZE``."""
    ordered = sorted(set(values))
    for start in range(0, len(ordered), SUPPRESSION_BATCH_SIZE):
        yield ordered[start : start + SUPPRESSION_BATCH_SIZE]


def lock_media_rows(*, session: Session, media_ids: Iterable[int]) -> None:
    """Lock media rows ``FOR KEY SHARE`` in ascending id order.

    This is the first lock of the shared order: it orders a writer after a
    media merge, which holds both records ``FOR UPDATE``. Rows already locked
    in this transaction are skipped; SQLite ignores the clause.

    Args:
        session: Session whose transaction takes the locks.
        media_ids: Media records about to gain or lose guarded rows.
    """
    locked = _transaction_cache(session, _LOCKED_MEDIA)
    for batch in _batches(set(media_ids) - locked):
        session.execute(
            select(Media.id)
            .where(Media.id.in_(batch))
            .order_by(Media.id)
            .with_for_update(read=True, key_share=True),
        ).all()
        locked.update(batch)


def lock_podcasts_of_episodes(
    *,
    session: Session,
    episode_ids: Iterable[int],
) -> None:
    """Lock the podcast rows of ``episode_ids`` ``FOR SHARE``.

    Each statement covers up to ``SUPPRESSION_BATCH_SIZE`` episodes and locks
    their podcasts in ascending id; the module docstring explains why the
    order across statements cannot deadlock.

    A takedown locks the same rows ``FOR UPDATE``, so a writer and a takedown
    covering the same podcast run one after the other. SQLite ignores the
    clause. Episodes already covered in this transaction are skipped.

    Args:
        session: Session whose transaction takes the locks.
        episode_ids: Episodes about to receive or lose guarded content.
    """
    locked = _transaction_cache(session, _LOCKED_EPISODES)
    for batch in _batches(set(episode_ids) - locked):
        session.execute(
            select(Podcast.id)
            .join(Episode, Episode.podcast_id == Podcast.id)
            .where(Episode.id.in_(batch))
            .order_by(Podcast.id)
            .with_for_update(read=True, of=Podcast),
        ).all()
        locked.update(batch)


def lock_requirements(session: Session, requirements: Iterable[_Requirement]) -> None:
    """Take media then podcast locks for a set of checks."""
    pending = list(requirements)
    lock_media_rows(
        session=session,
        media_ids={item.media_id for item in pending if item.media_id is not None},
    )
    lock_podcasts_of_episodes(
        session=session,
        episode_ids={item.episode_id for item in pending},
    )


def _suppressed(session: Session, requirements: Sequence[_Requirement]) -> set[Any]:
    """Return which requirements an approved takedown covers, batched."""
    found: set[Any] = set()
    for scope in ContentSuppressionScope:
        episodes = {
            item.episode_id
            for item in requirements
            if item.scope == scope and item.media_id is None
        }
        for batch in _batches(episodes):
            found.update(
                (scope, episode_id)
                for episode_id in session.scalars(
                    suppressed_episodes_statement(scope=scope, episode_ids=batch),
                )
            )
    pairs = {
        (item.episode_id, item.media_id)
        for item in requirements
        if item.media_id is not None
    }
    for batch in _batches(pairs):
        found.update(
            (ContentSuppressionScope.MENTIONS, episode_id, media_id)
            for episode_id, media_id in session.execute(
                suppressed_pairs_statement(batch),
            )
        )
    return found


def check_requirements(
    *,
    session: Session,
    requirements: Iterable[_Requirement],
) -> None:
    """Raise for the first requirement an approved takedown covers.

    Raises:
        ContentSuppressedError: If any write is covered by a suppression.
    """
    checked = _transaction_cache(session, _CHECKED)
    pending = sorted(
        set(requirements) - checked,
        key=lambda item: (item.scope.value, item.episode_id, item.media_id or 0),
    )
    if not pending:
        return
    found = _suppressed(session, pending)
    for requirement in pending:
        key: tuple[Any, ...] = (requirement.scope, requirement.episode_id)
        if requirement.media_id is not None:
            key = (*key, requirement.media_id)
        if key in found:
            raise ContentSuppressedError(
                scope=requirement.scope,
                episode_id=requirement.episode_id,
                media_id=requirement.media_id,
                row=requirement.row,
            )
    checked.update(pending)


def forget_checks(session: Session) -> None:
    """Drop remembered checks after this session changed suppression rows."""
    session.info.pop(_CHECKED, None)


def _touches_suppressions(session: Session) -> bool:
    return any(
        isinstance(row, ContentSuppression)
        for row in (*session.new, *session.dirty, *session.deleted)
    )


def lock_before_guarded_writes(
    session: Session,
    _flush_context: UOWTransaction,
    _instances: object,
) -> None:
    """Take locks and read origins before the flush writes any guarded row."""
    if _touches_suppressions(session):
        forget_checks(session)
    origins = _requirements(session, origins=True)
    session.info[_ORIGINS] = origins
    lock_requirements(session, origins | _requirements(session, origins=False))


def refuse_suppressed_writes(
    session: Session,
    _flush_context: UOWTransaction,
) -> None:
    """Check each guarded row the flush wrote; raising rolls the flush back."""
    requirements = _requirements(session, origins=False) | session.info.pop(
        _ORIGINS,
        set(),
    )
    if not requirements:
        return
    lock_requirements(session, requirements)
    check_requirements(session=session, requirements=requirements)


def _forget_bulk_suppression_changes(state: ORMExecuteState) -> None:
    """Forget checks when a bulk statement changes suppression rows."""
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    mapper = state.bind_mapper
    if mapper is not None and mapper.class_ is ContentSuppression:
        forget_checks(state.session)


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
        for key in (_LOCKED_EPISODES, _LOCKED_MEDIA, _CHECKED, _ORIGINS):
            session.info.pop(key, None)


# Registered as plain calls: the listens_for decorator is untyped under the
# dep-less CI mypy image and would mark these functions untyped.
event.listen(Session, "before_flush", lock_before_guarded_writes)
event.listen(Session, "after_flush", refuse_suppressed_writes)
event.listen(Session, "do_orm_execute", _forget_bulk_suppression_changes)
event.listen(Session, "after_transaction_end", _forget_transaction_checks)
for _model, _columns in _ORIGIN_COLUMNS.items():
    for _column in _columns:
        event.listen(
            getattr(_model, _column),
            "set",
            _load_replaced_value,
            active_history=True,
        )
