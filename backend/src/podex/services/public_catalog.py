"""Shared public catalog queries for podcast-facing endpoints."""

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_podcast_subquery,
    mention_count_by_episode_subquery,
    mention_count_by_podcast_subquery,
)
from podex.models import Episode, Podcast
from podex.schemas import EpisodeListResponse, EpisodeWithStats, PodcastWithStats


def _build_podcast_with_stats(
    *,
    podcast: Podcast,
    episode_count: int,
    mention_count: int,
) -> PodcastWithStats:
    """Build a podcast response with aggregated statistics.

    Args:
        podcast: Podcast model instance.
        episode_count: Number of episodes in the podcast.
        mention_count: Number of mentions across the podcast.

    Returns:
        Podcast response payload with statistics.
    """
    return PodcastWithStats(
        id=podcast.id,
        name=podcast.name,
        slug=podcast.slug,
        description=podcast.description,
        cover_url=podcast.cover_url,
        created_at=podcast.created_at,
        episode_count=episode_count,
        mention_count=mention_count,
    )


def _build_episode_with_stats(
    *,
    episode: Episode,
    mention_count: int,
) -> EpisodeWithStats:
    """Build an episode response with aggregated statistics.

    Args:
        episode: Episode model instance.
        mention_count: Number of mentions in the episode.

    Returns:
        Episode response payload with statistics.
    """
    return EpisodeWithStats(
        id=episode.id,
        podcast_id=episode.podcast_id,
        title=episode.title,
        episode_number=episode.episode_number,
        youtube_id=episode.youtube_id,
        published_at=episode.published_at,
        duration_seconds=episode.duration_seconds,
        thumbnail_url=episode.thumbnail_url,
        transcript_status=episode.transcript_status,
        created_at=episode.created_at,
        mention_count=mention_count,
    )


def list_podcasts_with_stats(
    *,
    db: Session,
) -> list[PodcastWithStats]:
    """List podcasts with aggregated counts.

    Args:
        db: Database session.

    Returns:
        Podcasts with episode and mention counts.
    """
    episode_counts = episode_count_by_podcast_subquery(db)
    mention_counts = mention_count_by_podcast_subquery(db)

    podcast_rows = (
        db.query(
            Podcast,
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
        )
        .outerjoin(episode_counts, Podcast.id == episode_counts.c.podcast_id)
        .outerjoin(mention_counts, Podcast.id == mention_counts.c.podcast_id)
        .all()
    )

    return [
        _build_podcast_with_stats(
            podcast=podcast,
            episode_count=episode_count,
            mention_count=mention_count,
        )
        for podcast, episode_count, mention_count in podcast_rows
    ]


def get_podcast_with_stats(
    *,
    db: Session,
    slug: str,
) -> PodcastWithStats | None:
    """Get a single podcast with aggregated counts.

    Args:
        db: Database session.
        slug: Podcast slug.

    Returns:
        Podcast with counts when found, otherwise ``None``.
    """
    episode_counts = episode_count_by_podcast_subquery(db)
    mention_counts = mention_count_by_podcast_subquery(db)

    podcast_row = (
        db.query(
            Podcast,
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
        )
        .outerjoin(episode_counts, Podcast.id == episode_counts.c.podcast_id)
        .outerjoin(mention_counts, Podcast.id == mention_counts.c.podcast_id)
        .filter(Podcast.slug == slug)
        .first()
    )
    if podcast_row is None:
        return None

    podcast, episode_count, mention_count = podcast_row
    return _build_podcast_with_stats(
        podcast=podcast,
        episode_count=episode_count,
        mention_count=mention_count,
    )


def list_podcast_episodes_with_stats(
    *,
    db: Session,
    slug: str,
    page: int,
    per_page: int,
) -> EpisodeListResponse | None:
    """Get paginated episodes for a podcast.

    Args:
        db: Database session.
        slug: Podcast slug.
        page: Requested page number.
        per_page: Number of items per page.

    Returns:
        Episode list response when the podcast exists, otherwise ``None``.
    """
    podcast = db.query(Podcast).filter(Podcast.slug == slug).first()
    if podcast is None:
        return None

    total = db.query(Episode).filter(Episode.podcast_id == podcast.id).count()
    mention_counts = mention_count_by_episode_subquery(db)

    episode_rows = (
        db.query(Episode)
        .filter(Episode.podcast_id == podcast.id)
        .outerjoin(mention_counts, Episode.id == mention_counts.c.episode_id)
        .add_columns(
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count")
        )
        .order_by(Episode.episode_number.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    items = [
        _build_episode_with_stats(
            episode=episode,
            mention_count=mention_count,
        )
        for episode, mention_count in episode_rows
    ]

    return EpisodeListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
    )
