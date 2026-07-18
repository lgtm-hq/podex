"""Authenticated account followed-podcast operations."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from podex.models import AccountFollowedPodcast, Podcast
from podex.services.podcast_queries import get_podcast


@dataclass(frozen=True, slots=True)
class FollowedPodcastData:
    """A followed association and its public catalog representation."""

    podcast: Podcast
    followed_at: datetime


def _get_public_podcast_by_id(
    *,
    db: Session,
    podcast_id: int,
) -> Podcast | None:
    """Resolve a canonical podcast record for account associations."""
    return get_podcast(db, podcast_id)


def list_followed_podcasts(*, db: Session, user_id: int) -> list[FollowedPodcastData]:
    """List a user's followed podcast sources, newest first."""
    rows = (
        db.query(AccountFollowedPodcast)
        .filter(AccountFollowedPodcast.user_id == user_id)
        .order_by(
            AccountFollowedPodcast.created_at.desc(),
            AccountFollowedPodcast.id.desc(),
        )
        .all()
    )
    followed: list[FollowedPodcastData] = []
    for row in rows:
        podcast = _get_public_podcast_by_id(db=db, podcast_id=row.podcast_id)
        if podcast is not None:
            followed.append(
                FollowedPodcastData(podcast=podcast, followed_at=row.created_at),
            )
    return followed


def follow_podcast(
    *,
    db: Session,
    user_id: int,
    podcast_id: int,
) -> FollowedPodcastData | None:
    """Idempotently follow an existing public podcast source."""
    podcast = _get_public_podcast_by_id(db=db, podcast_id=podcast_id)
    if podcast is None:
        return None
    existing = (
        db.query(AccountFollowedPodcast)
        .filter(
            AccountFollowedPodcast.user_id == user_id,
            AccountFollowedPodcast.podcast_id == podcast_id,
        )
        .first()
    )
    if existing is not None:
        return FollowedPodcastData(podcast=podcast, followed_at=existing.created_at)
    row = AccountFollowedPodcast(user_id=user_id, podcast_id=podcast_id)
    db.add(row)
    db.flush()
    return FollowedPodcastData(podcast=podcast, followed_at=row.created_at)


def unfollow_podcast(*, db: Session, user_id: int, podcast_id: int) -> bool:
    """Remove a public podcast source from a user's follows."""
    row = (
        db.query(AccountFollowedPodcast)
        .filter(
            AccountFollowedPodcast.user_id == user_id,
            AccountFollowedPodcast.podcast_id == podcast_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
