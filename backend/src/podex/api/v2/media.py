"""Public read endpoints for canonical media items.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.media_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException

from podex.api.deps import DbSession
from podex.models import Media, MediaType, Mention
from podex.schemas.media import MediaRead
from podex.schemas.mention import MentionRead
from podex.services import media_queries

router = APIRouter(prefix="/media", tags=["media"])


def list_media(db: DbSession, media_type: MediaType | None = None) -> list[Media]:
    """List media items, optionally filtered by type, ordered by title."""
    return media_queries.list_media(db, media_type=media_type)


def get_media(media_id: int, db: DbSession) -> Media:
    """Return a single media item by id."""
    media = media_queries.get_media(db, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def list_media_mentions(media_id: int, db: DbSession) -> list[Mention]:
    """List episode mentions of a media item."""
    mentions = media_queries.list_media_mentions(db, media_id)
    if mentions is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return mentions


router.add_api_route(
    "",
    list_media,
    methods=["GET"],
    response_model=list[MediaRead],
)
router.add_api_route(
    "/{media_id}/mentions",
    list_media_mentions,
    methods=["GET"],
    response_model=list[MentionRead],
)
router.add_api_route(
    "/{media_id}",
    get_media,
    methods=["GET"],
    response_model=MediaRead,
)
