"""Public read endpoints for canonical media items."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Media, MediaType, Mention
from podex.schemas.media import MediaRead
from podex.schemas.mention import MentionRead

router = APIRouter(prefix="/media", tags=["media"])


def list_media(
    db: DbSession,
    pagination: Pagination,
    media_type: MediaType | None = None,
) -> Page[MediaRead]:
    """List media items, optionally filtered by type, paginated."""
    count_stmt = select(func.count()).select_from(Media)
    statement = select(Media).order_by(Media.title)
    if media_type is not None:
        count_stmt = count_stmt.where(Media.type == media_type)
        statement = statement.where(Media.type == media_type)
    total = int(db.execute(count_stmt).scalar_one())
    statement = statement.offset(pagination.offset).limit(pagination.limit)
    rows = list(db.execute(statement).scalars().all())
    return Page[MediaRead](
        items=[MediaRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_media(media_id: int, db: DbSession) -> Media:
    """Return a single media item by id."""
    media = db.get(Media, media_id)
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return media


def list_media_mentions(
    media_id: int,
    db: DbSession,
    pagination: Pagination,
) -> Page[MentionRead]:
    """List episode mentions of a media item, paginated."""
    if db.get(Media, media_id) is None:
        raise HTTPException(status_code=404, detail="Media not found")
    total = int(
        db.execute(
            select(func.count())
            .select_from(Mention)
            .where(Mention.media_id == media_id),
        ).scalar_one(),
    )
    statement = (
        select(Mention)
        .where(Mention.media_id == media_id)
        .order_by(Mention.episode_id)
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = list(db.execute(statement).scalars().all())
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
