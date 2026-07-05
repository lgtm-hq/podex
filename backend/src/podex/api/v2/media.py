"""Public read endpoints for canonical media items."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from podex.api.deps import DbSession
from podex.models import Media, MediaType
from podex.schemas.media import MediaRead

router = APIRouter(prefix="/media", tags=["media"])


def list_media(db: DbSession, media_type: MediaType | None = None) -> list[Media]:
    """List media items, optionally filtered by type, ordered by title."""
    statement = select(Media).order_by(Media.title)
    if media_type is not None:
        statement = statement.where(Media.type == media_type)
    return list(db.execute(statement).scalars().all())


def get_media(media_id: int, db: DbSession) -> Media:
    """Return a single media item by id."""
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


router.add_api_route(
    "",
    list_media,
    methods=["GET"],
    response_model=list[MediaRead],
)
router.add_api_route(
    "/{media_id}",
    get_media,
    methods=["GET"],
    response_model=MediaRead,
)
