"""Episode API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from podex.database import get_db
from podex.schemas import EpisodeListResponse, EpisodeWithStats, MentionWithMedia
from podex.services.public_episodes import (
    get_episode_mentions,
    get_episode_with_stats,
    list_episodes_with_stats,
)

router = APIRouter(prefix="/episodes", tags=["episodes"])


@router.get("", response_model=EpisodeListResponse)
def list_episodes(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    podcast_id: int | None = None,
    db: Session = Depends(get_db),
) -> EpisodeListResponse:
    """List all episodes with pagination."""
    return list_episodes_with_stats(
        db=db,
        page=page,
        per_page=per_page,
        podcast_id=podcast_id,
    )


@router.get("/{episode_id}", response_model=EpisodeWithStats)
def get_episode(episode_id: int, db: Session = Depends(get_db)) -> EpisodeWithStats:
    """Get an episode by ID."""
    episode = get_episode_with_stats(db=db, episode_id=episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return episode


@router.get("/{episode_id}/mentions", response_model=list[MentionWithMedia])
def get_episode_mentions_route(
    episode_id: int,
    db: Session = Depends(get_db),
) -> list[MentionWithMedia]:
    """Get all mentions in an episode."""
    mentions = get_episode_mentions(db=db, episode_id=episode_id)
    if mentions is None:
        raise HTTPException(status_code=404, detail="Episode not found")

    return mentions
