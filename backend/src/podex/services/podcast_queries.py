"""Read-side query services for podcast sources.

The API layer stays thin by delegating list/get logic to these helpers. All
functions accept a SQLAlchemy ``Session`` and return ORM instances (or
``None`` for a missing lookup), so callers keep control of HTTP mapping and
response serialization.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from podex.models import Podcast


def list_podcasts(
    db: Session,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[Podcast]:
    """Return podcast sources ordered by display name.

    Args:
        db: Active SQLAlchemy session bound to the request.
        limit: Maximum number of rows to return; ``None`` returns all rows.
        offset: Number of leading rows to skip.

    Returns:
        Podcast rows sorted alphabetically by ``name``.
    """
    statement = select(Podcast).order_by(Podcast.name).offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    result = db.execute(statement)
    return list(result.scalars().all())


def count_podcasts(db: Session) -> int:
    """Return the total number of podcast sources.

    Args:
        db: Active SQLAlchemy session bound to the request.

    Returns:
        The row count across the whole ``podcasts`` table.
    """
    return int(db.execute(select(func.count()).select_from(Podcast)).scalar_one())


def get_podcast(db: Session, podcast_id: int) -> Podcast | None:
    """Return the podcast source with ``podcast_id`` or ``None``.

    Args:
        db: Active SQLAlchemy session bound to the request.
        podcast_id: Primary key of the podcast to fetch.

    Returns:
        The matching Podcast row, or ``None`` when no such row exists.
    """
    # Explicit local annotation keeps the return well-typed under the CI
    # lint image where ``Session.get`` resolves to ``Any``.
    podcast: Podcast | None = db.get(Podcast, podcast_id)
    return podcast
