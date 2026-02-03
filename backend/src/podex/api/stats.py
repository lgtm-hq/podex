"""Statistics API endpoints."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from podex.database import get_db
from podex.services.trend_queries import (
    get_overview_stats as fetch_overview_stats,
)
from podex.services.trend_queries import (
    get_stats_by_type as fetch_stats_by_type,
)
from podex.services.trend_queries import get_top_mentioned_media

router = APIRouter(prefix="/stats", tags=["stats"])


class OverviewStats(BaseModel):
    """Overall statistics."""

    total_podcasts: int
    total_episodes: int
    total_media: int
    total_mentions: int
    total_books: int
    total_movies: int


class TypeStats(BaseModel):
    """Statistics by media type."""

    type: str
    count: int
    mention_count: int


class TopMentioned(BaseModel):
    """Top mentioned media item."""

    id: int
    title: str
    type: str
    author: str | None
    mention_count: int


@router.get("/overview", response_model=OverviewStats)
def get_overview_stats(db: Session = Depends(get_db)) -> OverviewStats:
    """Get overall statistics."""
    stats = fetch_overview_stats(db=db)

    return OverviewStats(
        total_podcasts=stats.total_podcasts,
        total_episodes=stats.total_episodes,
        total_media=stats.total_media,
        total_mentions=stats.total_mentions,
        total_books=stats.total_books,
        total_movies=stats.total_movies,
    )


@router.get("/by-type", response_model=list[TypeStats])
def get_stats_by_type(db: Session = Depends(get_db)) -> list[TypeStats]:
    """Get statistics grouped by media type."""
    return [
        TypeStats(
            type=item.type.value,
            count=item.count,
            mention_count=item.mention_count,
        )
        for item in fetch_stats_by_type(db=db)
    ]


@router.get("/top-mentioned", response_model=list[TopMentioned])
def get_top_mentioned(
    limit: int = 10,
    type: str | None = None,
    db: Session = Depends(get_db),
) -> list[TopMentioned]:
    """Get top mentioned media items."""
    return [
        TopMentioned(
            id=item.id,
            title=item.title,
            type=item.type.value,
            author=item.author,
            mention_count=item.mention_count,
        )
        for item in get_top_mentioned_media(
            db=db,
            limit=limit,
            media_type=type,
        )
    ]
