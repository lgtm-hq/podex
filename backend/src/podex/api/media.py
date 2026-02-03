"""Media API endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from podex.database import get_db
from podex.models import MediaType
from podex.schemas import (
    MediaDetail,
    MediaListResponse,
    MediaResponse,
    MentionWithEpisode,
)
from podex.services.public_media import (
    get_media_detail_by_id,
    get_media_mentions,
    get_top_media_with_stats,
    list_media_with_stats,
    search_media_with_stats,
)

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=MediaListResponse)
def list_media(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    type: list[MediaType] | None = Query(None),
    sort: Literal["mention_count", "title", "created_at"] = "mention_count",
    order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
) -> MediaListResponse:
    """List all media with pagination and filtering."""
    return list_media_with_stats(
        db=db,
        page=page,
        per_page=per_page,
        media_types=type,
        sort=sort,
        order=order,
    )


@router.get("/search", response_model=MediaListResponse)
def search_media(
    q: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    type: list[MediaType] | None = Query(None),
    db: Session = Depends(get_db),
) -> MediaListResponse:
    """Search media by title or author."""
    return search_media_with_stats(
        db=db,
        query_text=q,
        page=page,
        per_page=per_page,
        media_types=type,
    )


@router.get("/top", response_model=list[MediaResponse])
def get_top_media(
    limit: int = 10,
    type: str | None = None,
    db: Session = Depends(get_db),
) -> list[MediaResponse]:
    """Get top mentioned media."""
    return get_top_media_with_stats(
        db=db,
        limit=limit,
        media_type=type,
    )


@router.get("/{media_id}", response_model=MediaDetail)
def get_media(media_id: int, db: Session = Depends(get_db)) -> MediaDetail:
    """Get media detail with all mentions and enrichment data."""
    media = get_media_detail_by_id(db=db, media_id=media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


@router.get("/{media_id}/mentions", response_model=list[MentionWithEpisode])
def get_media_mentions_endpoint(
    media_id: int,
    db: Session = Depends(get_db),
) -> list[MentionWithEpisode]:
    """Get mention occurrences for a media item."""
    media = get_media_detail_by_id(db=db, media_id=media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return get_media_mentions(db=db, media_id=media_id)
