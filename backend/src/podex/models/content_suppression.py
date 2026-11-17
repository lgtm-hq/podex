"""Durable publication suppressions recorded by approved takedowns.

A takedown deletes the rows that exist when it runs. A ``ContentSuppression``
row is what stays behind: it states that content of one scope must not exist
again for a podcast, an episode or one ``(episode, media)`` mention pair.

``suppressed_clause`` is the single definition of "suppressed". Public reads
use it to filter rows, and the write guard (``podex.models.suppression_guard``)
uses it to refuse writes, so a reader and a writer cannot disagree about what
a suppression covers.
"""

from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Select,
    String,
    Table,
    and_,
    event,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Mapped, aliased, mapped_column
from sqlalchemy.sql import Subquery

from podex.models.base import Base
from podex.models.episode import Episode


class ContentSuppressionSubjectType(StrEnum):
    """What a suppression row covers."""

    PODCAST = auto()
    EPISODE = auto()
    MENTION_PAIR = auto()


class ContentSuppressionScope(StrEnum):
    """Which kind of content a suppression row keeps from being written."""

    MENTIONS = auto()
    DERIVATIVES = auto()


class ContentSuppressedError(ValueError):
    """Raised when a write would recreate content an approved takedown removed.

    Also raised when a flush deletes a podcast or episode that a suppression
    covers, because the suppression is keyed by that row's id.

    Attributes:
        scope: Suppression scope that covers the write.
        episode_id: Episode the refused content belongs to, or ``None`` when
            only a podcast is named.
        media_id: Media of the refused mention, for mention writes.
        podcast_id: Podcast the refusal names when it names no episode.
        row: The refused row and why, such as ``Mention 5 (moved out of
            its pair)``, when a flush refused it.
    """

    def __init__(
        self,
        *,
        scope: ContentSuppressionScope,
        episode_id: int | None = None,
        media_id: int | None = None,
        podcast_id: int | None = None,
        row: str = "",
    ) -> None:
        subject = (
            f"episode {episode_id}"
            if episode_id is not None
            else f"podcast {podcast_id}"
        )
        if media_id is not None:
            subject = f"{subject} and media {media_id}"
        message = f"{scope.value} for {subject} are suppressed by an approved takedown"
        if row:
            message = f"{message}; refused write: {row}"
        super().__init__(message)
        self.scope = scope
        self.episode_id = episode_id
        self.media_id = media_id
        self.podcast_id = podcast_id
        self.row = row


class RetryableLockError(ValueError):
    """Content changed, or a lock was busy, while locks were being taken.

    Nothing was written; the operation can be retried as it is. Routes map
    ``ValueError`` to 409.
    """


class ContentSuppression(Base):
    """One approved takedown's suppression of one scope for one subject."""

    __tablename__ = "content_suppressions"
    __table_args__ = (
        CheckConstraint(
            "(subject_type = 'podcast' AND podcast_id IS NOT NULL"
            " AND episode_id IS NULL AND media_id IS NULL)"
            " OR (subject_type = 'episode' AND podcast_id IS NULL"
            " AND episode_id IS NOT NULL AND media_id IS NULL)"
            " OR (subject_type = 'mention_pair' AND podcast_id IS NULL"
            " AND episode_id IS NOT NULL AND media_id IS NOT NULL)",
            name="ck_content_suppressions_subject_ids",
        ),
        Index("ix_content_suppressions_scope_episode_id", "scope", "episode_id"),
        Index("ix_content_suppressions_scope_podcast_id", "scope", "podcast_id"),
    )

    id: "Mapped[int]" = mapped_column(primary_key=True)
    suppression_key: "Mapped[str]" = mapped_column(
        String(120),
        unique=True,
        index=True,
    )
    takedown_request_id: "Mapped[int]" = mapped_column(
        ForeignKey("takedown_requests.id"),
        index=True,
    )
    subject_type: "Mapped[str]" = mapped_column(String(32))
    scope: "Mapped[str]" = mapped_column(String(32))
    # A suppression is keyed by these ids, so the database refuses to delete
    # the row it covers instead of cascading the suppression away (ADR 0017).
    podcast_id: "Mapped[int | None]" = mapped_column(
        ForeignKey("podcasts.id", ondelete="RESTRICT"),
    )
    episode_id: "Mapped[int | None]" = mapped_column(
        ForeignKey("episodes.id", ondelete="RESTRICT"),
    )
    media_id: "Mapped[int | None]" = mapped_column(
        ForeignKey("media.id"),
        index=True,
    )
    created_at: "Mapped[datetime]" = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )


class ContentSuppressionVersion(Base):
    """A counter every suppression-relevant write increases by one.

    Takedown execution, media merges and splits that carry or delete
    suppressed content, and the backfill bump it in their transaction. Caches
    of user-facing aggregates key on it: unlike a count or highest id of
    suppression rows it never repeats, even after rows are deleted.
    """

    __tablename__ = "content_suppression_versions"

    id: "Mapped[int]" = mapped_column(primary_key=True)
    version: "Mapped[int]" = mapped_column(Integer, default=0)


def _seed_version_row(table: Table, connection: Connection, **_kw: object) -> None:
    """Insert the single counter row when the table is created from the models.

    Revision ``0022`` seeds it the same way. With the row always present, a
    bump only updates it under a row lock, so two first bumps cannot race to
    insert it.
    """
    connection.execute(table.insert().values(id=1, version=0))


event.listen(ContentSuppressionVersion.__table__, "after_create", _seed_version_row)


def suppression_key(
    *,
    subject_type: ContentSuppressionSubjectType,
    scope: ContentSuppressionScope,
    subject_ids: tuple[int, ...],
) -> str:
    """Return the deduplication key for one subject and scope.

    A unique constraint across the nullable id columns would not deduplicate
    on SQLite or PostgreSQL, because ``NULL`` never equals ``NULL``.

    Args:
        subject_type: What the suppression covers.
        scope: Which content it suppresses.
        subject_ids: Podcast id, episode id, or episode and media ids.

    Returns:
        A stable key such as ``mention_pair:12:34:mentions``.
    """
    ids = ":".join(str(subject_id) for subject_id in subject_ids)
    return f"{subject_type.value}:{ids}:{scope.value}"


def suppressed_clause(
    *,
    scope: ContentSuppressionScope,
    episode_id: ColumnElement[int] | int,
    media_id: ColumnElement[int] | int | None = None,
) -> ColumnElement[bool]:
    """Return SQL that is true when ``scope`` content of an episode is suppressed.

    The episode is covered by a suppression of its podcast (including episodes
    discovered after the approval), of the episode itself, or, when
    ``media_id`` is given, of that ``(episode, media)`` mention pair.

    The episode's podcast is read through its own alias, joined inside the
    ``EXISTS``, so a column of the enclosing query (a ``Mention`` or an
    ``Episode`` column) correlates to the outer row instead of the lookup
    comparing a table with itself; a literal id works the same way.

    Args:
        scope: Suppression scope to test.
        episode_id: Episode id column (for reads) or value (for writes).
        media_id: Media id column or value for mention-pair coverage.

    Returns:
        A boolean ``EXISTS`` expression usable in ``WHERE`` or ``SELECT``.
    """
    covered = aliased(Episode, name="suppression_covered_episode")
    covering = [
        and_(
            ContentSuppression.subject_type
            == ContentSuppressionSubjectType.PODCAST.value,
            ContentSuppression.podcast_id == covered.podcast_id,
        ),
        and_(
            ContentSuppression.subject_type
            == ContentSuppressionSubjectType.EPISODE.value,
            ContentSuppression.episode_id == covered.id,
        ),
    ]
    if media_id is not None:
        covering.append(
            and_(
                ContentSuppression.subject_type
                == ContentSuppressionSubjectType.MENTION_PAIR.value,
                ContentSuppression.episode_id == covered.id,
                ContentSuppression.media_id == media_id,
            ),
        )
    return (
        select(ContentSuppression.id)
        .join(covered, covered.id == episode_id)
        .where(ContentSuppression.scope == scope.value, or_(*covering))
        .exists()
    )


SUPPRESSION_BATCH_SIZE = 5000
"""Ids or pairs checked per statement.

At two bound parameters per pair this stays well under SQLite's and
PostgreSQL's parameter limits.
"""

_UNION_GROUP_SIZE = 400
"""Literal rows per ``UNION ALL`` group, under SQLite's 500-term limit."""


def batches(values: Iterable[Any]) -> Iterator[list[Any]]:
    """Split sorted values into statements of ``SUPPRESSION_BATCH_SIZE``.

    Args:
        values: Ids, pairs or keys; duplicates are dropped.

    Yields:
        Ascending batches of at most ``SUPPRESSION_BATCH_SIZE`` values.
    """
    ordered = sorted(set(values))
    for start in range(0, len(ordered), SUPPRESSION_BATCH_SIZE):
        yield ordered[start : start + SUPPRESSION_BATCH_SIZE]


def suppressed_episodes_statement(
    *,
    scope: ContentSuppressionScope,
    episode_ids: Sequence[int],
) -> Select[int]:
    """Return a select of the given episodes that ``scope`` suppresses.

    Args:
        scope: Suppression scope to test.
        episode_ids: At most ``SUPPRESSION_BATCH_SIZE`` episode ids.

    Returns:
        A select of the suppressed subset's ids.
    """
    return select(Episode.id).where(
        Episode.id.in_(episode_ids),
        suppressed_clause(scope=scope, episode_id=Episode.id),
    )


def suppressed_pairs_statement(
    pairs: Sequence[tuple[int, int]],
) -> Select[int, int]:
    """Return a select of the given ``(episode, media)`` pairs that are hidden.

    The pairs become a ``UNION ALL`` of literal rows, so the check is the same
    ``suppressed_clause`` public reads use, on SQLite and PostgreSQL alike.

    Args:
        pairs: At most ``SUPPRESSION_BATCH_SIZE`` pairs.

    Returns:
        A select of the suppressed pairs.
    """
    rows: list[Select[int, int]] = []
    for episode_id, media_id in pairs:
        row: Select[int, int] = select(
            literal(episode_id, Integer).label("episode_id"),
            literal(media_id, Integer).label("media_id"),
        )
        rows.append(row)
    groups = [
        _union(rows[start : start + _UNION_GROUP_SIZE], name=f"pairs_{start}")
        for start in range(0, len(rows), _UNION_GROUP_SIZE)
    ]
    regrouped: list[Select[int, int]] = [
        select(group.c.episode_id, group.c.media_id) for group in groups
    ]
    pending = _union(
        regrouped,
        name="pending_pairs",
    )
    return select(pending.c.episode_id, pending.c.media_id).where(
        suppressed_clause(
            scope=ContentSuppressionScope.MENTIONS,
            episode_id=pending.c.episode_id,
            media_id=pending.c.media_id,
        ),
    )


def _union(selects: Sequence[Select[int, int]], *, name: str) -> Subquery:
    """Return one select, or a ``UNION ALL`` of several, as a named subquery."""
    if len(selects) == 1:
        return selects[0].subquery(name)
    return union_all(*selects).subquery(name)
