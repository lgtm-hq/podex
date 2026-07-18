"""Read-side query services for podcast episodes.

Route handlers under ``podex.api.v2.episodes`` delegate here so the API
modules only translate service outcomes to HTTP responses. Services stay
storage-focused: they accept a SQLAlchemy ``Session`` and return ORM rows
or ``None`` for missing lookups.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Episode, Mention


def list_episodes(db: Session, podcast_id: int | None = None) -> list[Episode]:
    """Return episodes ordered newest-first, optionally filtered by podcast.

    Args:
        db: Active SQLAlchemy session bound to the request.
        podcast_id: If provided, restrict results to a single podcast source.

    Returns:
        Episode rows sorted by ``published_at`` descending.
    """
    statement = select(Episode).order_by(Episode.published_at.desc())
    if podcast_id is not None:
        statement = statement.where(Episode.podcast_id == podcast_id)
    return list(db.execute(statement).scalars().all())


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


def list_episode_mentions(db: Session, episode_id: int) -> list[Mention] | None:
    """Return an episode's mentions in timestamp order.

    Args:
        db: Active SQLAlchemy session bound to the request.
        episode_id: Primary key of the parent episode.

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
    )
    return list(db.execute(statement).scalars().all())
