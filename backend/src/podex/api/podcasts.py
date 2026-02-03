"""Podcast API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from podex.database import get_db
from podex.schemas import EpisodeListResponse, PodcastWithStats
from podex.services.public_catalog import (
    get_podcast_with_stats,
    list_podcast_episodes_with_stats,
    list_podcasts_with_stats,
)

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


@router.get("", response_model=list[PodcastWithStats])
def list_podcasts(db: Session = Depends(get_db)) -> list[PodcastWithStats]:
    """List all podcasts with statistics."""
    return list_podcasts_with_stats(db=db)


@router.get("/{slug}", response_model=PodcastWithStats)
def get_podcast(slug: str, db: Session = Depends(get_db)) -> PodcastWithStats:
    """Get a podcast by slug."""
    podcast = get_podcast_with_stats(db=db, slug=slug)
    if podcast is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return podcast


@router.get("/{slug}/episodes", response_model=EpisodeListResponse)
def get_podcast_episodes(
    slug: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> EpisodeListResponse:
    """Get episodes for a podcast."""
    episodes = list_podcast_episodes_with_stats(
        db=db,
        slug=slug,
        page=page,
        per_page=per_page,
    )
    if episodes is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return episodes
