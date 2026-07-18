"""Read-side query services for podcast sources.

The API layer stays thin by delegating list/get logic to these helpers. All
functions accept a SQLAlchemy ``Session`` and return ORM instances (or
``None`` for a missing lookup), so callers keep control of HTTP mapping and
response serialization.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Podcast


def list_podcasts(db: Session) -> list[Podcast]:
    """Return every podcast source, ordered by display name.

    Args:
        db: Active SQLAlchemy session bound to the request.

    Returns:
        Podcast rows sorted alphabetically by ``name``.
    """
    result = db.execute(select(Podcast).order_by(Podcast.name))
    return list(result.scalars().all())


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
