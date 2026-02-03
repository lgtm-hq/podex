"""Shared podcast management query services for ops surfaces."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, auto
from typing import Any, Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_podcast_subquery,
    mention_count_by_podcast_subquery,
)
from podex.models import Podcast, PodcastStatus


class PodcastSourceType(StrEnum):
    """Supported source filters for ops podcast management views."""

    RSS = auto()
    SPOTIFY = auto()
    APPLE = auto()
    YOUTUBE = auto()
    PODSCRIPTS = auto()


@dataclass(frozen=True, slots=True)
class OpsPodcastSourceData:
    """Source identifiers associated with a managed podcast."""

    rss_url: str | None
    spotify_id: str | None
    apple_id: str | None
    youtube_channel_id: str | None
    podscripts_slug: str | None


@dataclass(frozen=True, slots=True)
class OpsPodcastSummaryData:
    """Podcast summary for ops catalog management views."""

    id: int
    name: str
    slug: str
    status: PodcastStatus
    description: str | None
    cover_url: str | None
    created_at: datetime
    discovery_source: str | None
    episode_count: int
    mention_count: int
    sources: OpsPodcastSourceData


@dataclass(frozen=True, slots=True)
class OpsPodcastListData:
    """Paginated podcast management payload for ops surfaces."""

    items: list[OpsPodcastSummaryData]
    total: int
    page: int
    per_page: int


def _build_ops_podcast_summary(
    *,
    podcast: Podcast,
    episode_count: int,
    mention_count: int,
) -> OpsPodcastSummaryData:
    """Build an ops podcast summary with aggregated counts.

    Args:
        podcast: Podcast model instance.
        episode_count: Number of episodes in the podcast.
        mention_count: Number of mentions across the podcast.

    Returns:
        Ops podcast summary payload.
    """
    return OpsPodcastSummaryData(
        id=podcast.id,
        name=podcast.name,
        slug=podcast.slug,
        status=PodcastStatus(podcast.status),
        description=podcast.description,
        cover_url=podcast.cover_url,
        created_at=podcast.created_at,
        discovery_source=podcast.discovery_source,
        episode_count=episode_count,
        mention_count=mention_count,
        sources=OpsPodcastSourceData(
            rss_url=podcast.rss_url,
            spotify_id=podcast.spotify_id,
            apple_id=podcast.apple_id,
            youtube_channel_id=podcast.youtube_channel_id,
            podscripts_slug=podcast.podscripts_slug,
        ),
    )


def _apply_source_filter(*, query: Any, source: PodcastSourceType) -> Any:
    """Apply a source-presence filter to an ops podcast query.

    Args:
        query: SQLAlchemy query to filter.
        source: Source type to require.

    Returns:
        Filtered query.
    """
    if source is PodcastSourceType.RSS:
        return query.filter(Podcast.rss_url.isnot(None))
    if source is PodcastSourceType.SPOTIFY:
        return query.filter(Podcast.spotify_id.isnot(None))
    if source is PodcastSourceType.APPLE:
        return query.filter(Podcast.apple_id.isnot(None))
    if source is PodcastSourceType.YOUTUBE:
        return query.filter(Podcast.youtube_channel_id.isnot(None))
    return query.filter(Podcast.podscripts_slug.isnot(None))


def list_ops_podcasts(
    *,
    db: Session,
    page: int,
    per_page: int,
    status: PodcastStatus | None = None,
    source: PodcastSourceType | None = None,
    sort: Literal["created_at", "name", "episode_count", "mention_count"] = (
        "created_at"
    ),
    order: Literal["asc", "desc"] = "desc",
) -> OpsPodcastListData:
    """List podcasts for ops catalog management surfaces.

    Args:
        db: Database session.
        page: Requested page number.
        per_page: Number of items per page.
        status: Optional podcast status filter.
        source: Optional source-presence filter.
        sort: Sort field.
        order: Sort direction.

    Returns:
        Paginated ops podcast response.
    """
    episode_counts = episode_count_by_podcast_subquery(db)
    mention_counts = mention_count_by_podcast_subquery(db)

    query = (
        db.query(
            Podcast,
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
        )
        .outerjoin(episode_counts, Podcast.id == episode_counts.c.podcast_id)
        .outerjoin(mention_counts, Podcast.id == mention_counts.c.podcast_id)
    )

    if status is not None:
        query = query.filter(Podcast.status == status.value)

    if source is not None:
        query = _apply_source_filter(query=query, source=source)

    total = query.count()

    if sort == "name":
        sort_column = Podcast.name
    elif sort == "episode_count":
        sort_column = func.coalesce(episode_counts.c.episode_count, 0)
    elif sort == "mention_count":
        sort_column = func.coalesce(mention_counts.c.mention_count, 0)
    else:
        sort_column = Podcast.created_at

    sort_expression = sort_column.desc() if order == "desc" else sort_column.asc()
    podcast_rows = (
        query.order_by(sort_expression, Podcast.id.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return OpsPodcastListData(
        items=[
            _build_ops_podcast_summary(
                podcast=podcast,
                episode_count=episode_count,
                mention_count=mention_count,
            )
            for podcast, episode_count, mention_count in podcast_rows
        ],
        total=total,
        page=page,
        per_page=per_page,
    )


def get_ops_podcast_by_id(
    *,
    db: Session,
    podcast_id: int,
) -> OpsPodcastSummaryData | None:
    """Get a single podcast summary for ops catalog management views.

    Args:
        db: Database session.
        podcast_id: Internal podcast identifier.

    Returns:
        Ops podcast summary when found, otherwise ``None``.
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
        .filter(Podcast.id == podcast_id)
        .first()
    )
    if podcast_row is None:
        return None

    podcast, episode_count, mention_count = podcast_row
    return _build_ops_podcast_summary(
        podcast=podcast,
        episode_count=episode_count,
        mention_count=mention_count,
    )
