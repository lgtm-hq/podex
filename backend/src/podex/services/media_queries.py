"""Read-side query services for canonical media items.

Route handlers under ``podex.api.v2.media`` delegate here so the API modules
only translate service outcomes into HTTP responses. Services accept a
SQLAlchemy ``Session`` and return ORM rows or ``None`` for missing lookups.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from podex.models import Media, MediaType, Mention


def list_media(
    db: Session,
    media_type: MediaType | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Media]:
    """Return media items ordered by title, optionally filtered by type.

    Args:
        db: Active SQLAlchemy session bound to the request.
        media_type: If provided, restrict results to this media kind.
        limit: Maximum number of rows to return; ``None`` returns all rows.
        offset: Number of leading rows to skip.

    Returns:
        Media rows sorted alphabetically by ``title``.
    """
    statement = select(Media).order_by(Media.title)
    if media_type is not None:
        statement = statement.where(Media.type == media_type)
    statement = statement.offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())


def count_media(db: Session, media_type: MediaType | None = None) -> int:
    """Return the number of media items, optionally filtered by type.

    Args:
        db: Active SQLAlchemy session bound to the request.
        media_type: If provided, restrict the count to this media kind.

    Returns:
        The matching row count.
    """
    statement = select(func.count()).select_from(Media)
    if media_type is not None:
        statement = statement.where(Media.type == media_type)
    return int(db.execute(statement).scalar_one())


def get_media(db: Session, media_id: int) -> Media | None:
    """Return the media item with ``media_id`` or ``None``.

    Args:
        db: Active SQLAlchemy session bound to the request.
        media_id: Primary key of the media item to fetch.

    Returns:
        The matching Media row, or ``None`` when no such row exists.
    """
    # Explicit local annotation keeps the return well-typed under the CI
    # lint image where ``Session.get`` resolves to ``Any``.
    media: Media | None = db.get(Media, media_id)
    return media


def list_media_mentions(
    db: Session,
    media_id: int,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Mention] | None:
    """Return the mentions that reference a media item.

    Args:
        db: Active SQLAlchemy session bound to the request.
        media_id: Primary key of the media item.
        limit: Maximum number of rows to return; ``None`` returns all rows.
        offset: Number of leading rows to skip.

    Returns:
        Mentions ordered by ``episode_id`` when the media item exists, or
        ``None`` to signal that the parent media item is unknown so the caller
        can translate that into a 404 response.
    """
    if db.get(Media, media_id) is None:
        return None
    statement = (
        select(Mention)
        .where(Mention.media_id == media_id)
        .order_by(Mention.episode_id)
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())


def count_media_mentions(db: Session, media_id: int) -> int:
    """Return the number of mentions referencing a media item.

    Args:
        db: Active SQLAlchemy session bound to the request.
        media_id: Primary key of the media item.

    Returns:
        The mention count for the media item (0 when the item is unknown).
    """
    statement = (
        select(func.count()).select_from(Mention).where(Mention.media_id == media_id)
    )
    return int(db.execute(statement).scalar_one())
