"""Reusable query helpers for common subquery patterns."""

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.sql import Subquery

from podex.models import Episode, Mention


def mention_count_by_media_subquery(db: Session) -> Subquery:
    """Create subquery for mention counts grouped by media_id.

    Args:
        db: Database session.

    Returns:
        Subquery with columns: media_id, mention_count.
    """
    return (
        db.query(
            Mention.media_id,
            func.count(Mention.id).label("mention_count"),
        )
        .group_by(Mention.media_id)
        .subquery()
    )


def episode_count_by_media_subquery(db: Session) -> Subquery:
    """Create subquery for unique episode counts grouped by media_id.

    Args:
        db: Database session.

    Returns:
        Subquery with columns: media_id, episode_count.
    """
    return (
        db.query(
            Mention.media_id,
            func.count(func.distinct(Mention.episode_id)).label("episode_count"),
        )
        .group_by(Mention.media_id)
        .subquery()
    )


def mention_count_by_episode_subquery(db: Session) -> Subquery:
    """Create subquery for mention counts grouped by episode_id.

    Args:
        db: Database session.

    Returns:
        Subquery with columns: episode_id, mention_count.
    """
    return (
        db.query(
            Mention.episode_id,
            func.count(Mention.id).label("mention_count"),
        )
        .group_by(Mention.episode_id)
        .subquery()
    )


def episode_count_by_podcast_subquery(db: Session) -> Subquery:
    """Create subquery for episode counts grouped by podcast_id.

    Args:
        db: Database session.

    Returns:
        Subquery with columns: podcast_id, episode_count.
    """
    return (
        db.query(
            Episode.podcast_id,
            func.count(Episode.id).label("episode_count"),
        )
        .group_by(Episode.podcast_id)
        .subquery()
    )


def mention_count_by_podcast_subquery(db: Session) -> Subquery:
    """Create subquery for mention counts grouped by podcast_id (via episodes).

    Args:
        db: Database session.

    Returns:
        Subquery with columns: podcast_id, mention_count.
    """
    return (
        db.query(
            Episode.podcast_id,
            func.count(Mention.id).label("mention_count"),
        )
        .join(Mention, Mention.episode_id == Episode.id)
        .group_by(Episode.podcast_id)
        .subquery()
    )
