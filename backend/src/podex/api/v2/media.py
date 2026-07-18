"""Public read endpoints for canonical media items.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.media_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Media, MediaType
from podex.schemas.media import MediaRead
from podex.schemas.mention import MentionRead
from podex.services import media_queries

router = APIRouter(prefix="/media", tags=["media"])


def list_media(
    db: DbSession,
    pagination: Pagination,
    media_type: MediaType | None = None,
) -> Page[MediaRead]:
    """List media items, optionally filtered by type, paginated."""
    total = media_queries.count_media(db, media_type=media_type)
    rows = media_queries.list_media(
        db,
        media_type=media_type,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[MediaRead](
        items=[MediaRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_media(media_id: int, db: DbSession) -> Media:
    """Return a single media item by id."""
    media = media_queries.get_media(db, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def list_media_mentions(
    media_id: int,
    db: DbSession,
    pagination: Pagination,
) -> Page[MentionRead]:
    """List episode mentions of a media item, paginated."""
    rows = media_queries.list_media_mentions(
        db,
        media_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="Media not found")
    total = media_queries.count_media_mentions(db, media_id)
    return Page[MentionRead](
        items=[MentionRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


router.add_api_route(
    "",
    list_media,
    methods=["GET"],
    response_model=Page[MediaRead],
)
router.add_api_route(
    "/{media_id}/mentions",
    list_media_mentions,
    methods=["GET"],
    response_model=Page[MentionRead],
)
router.add_api_route(
    "/{media_id}",
    get_media,
    methods=["GET"],
    response_model=MediaRead,
)
