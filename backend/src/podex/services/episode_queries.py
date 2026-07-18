"""Read-side query services for podcast episodes.

Route handlers under ``podex.api.v2.episodes`` delegate here so the API
modules only translate service outcomes to HTTP responses. Services stay
storage-focused: they accept a SQLAlchemy ``Session`` and return ORM rows
or ``None`` for missing lookups.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from podex.models import Episode, Mention


def list_episodes(
    db: Session,
    podcast_id: int | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Episode]:
    """Return episodes ordered newest-first, optionally filtered by podcast.

    Args:
        db: Active SQLAlchemy session bound to the request.
        podcast_id: If provided, restrict results to a single podcast source.
        limit: Maximum number of rows to return; ``None`` returns all rows.
        offset: Number of leading rows to skip.

    Returns:
        Episode rows sorted by ``published_at`` descending.
    """
    statement = select(Episode).order_by(Episode.published_at.desc())
    if podcast_id is not None:
        statement = statement.where(Episode.podcast_id == podcast_id)
    statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())


def count_episodes(db: Session, podcast_id: int | None = None) -> int:
    """Return the number of episodes, optionally filtered by podcast.

    Args:
        db: Active SQLAlchemy session bound to the request.
        podcast_id: If provided, restrict the count to a single podcast source.

    Returns:
        The matching row count.
    """
    statement = select(func.count()).select_from(Episode)
    if podcast_id is not None:
        statement = statement.where(Episode.podcast_id == podcast_id)
    return int(db.execute(statement).scalar_one())


def get_episode(db: Session, episode_id: int) -> Episode | None:
    """Return the episode with ``episode_id`` or ``None``.

    Args:
        db: Active SQLAlchemy session bound to the request.
        episode_id: Primary key of the episode to fetch.

    Returns:
        The matching Episode row, or ``None`` when no such row exists.
    """
    # Explicit local annotation keeps the return well-typed under the CI
    # lint image where ``Session.get`` resolves to ``Any``.
    episode: Episode | None = db.get(Episode, episode_id)
    return episode


def list_episode_mentions(
    db: Session,
    episode_id: int,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Mention] | None:
    """Return an episode's mentions in timestamp order.

    Args:
        db: Active SQLAlchemy session bound to the request.
        episode_id: Primary key of the parent episode.
        limit: Maximum number of rows to return; ``None`` returns all rows.
        offset: Number of leading rows to skip.

    Returns:
        Mentions ordered by ``timestamp_seconds`` when the episode exists, or
        ``None`` to signal that the parent episode is unknown so the caller can
        translate that into a 404 response.
    """
    if db.get(Episode, episode_id) is None:
        return None
    statement = (
        select(Mention)
        .where(Mention.episode_id == episode_id)
        .order_by(Mention.timestamp_seconds)
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())


def count_episode_mentions(db: Session, episode_id: int) -> int:
    """Return the number of mentions attached to an episode.

    Args:
        db: Active SQLAlchemy session bound to the request.
        episode_id: Primary key of the parent episode.

    Returns:
        The mention count for the episode (0 when the episode is unknown).
    """
    statement = (
        select(func.count())
        .select_from(Mention)
        .where(Mention.episode_id == episode_id)
    )
    return int(db.execute(statement).scalar_one())
