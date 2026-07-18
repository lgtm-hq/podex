"""Public read endpoints for podcast sources."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Podcast
from podex.schemas.podcast import PodcastRead

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def list_podcasts(db: DbSession, pagination: Pagination) -> Page[PodcastRead]:
    """List podcast sources ordered by name, paginated by ``limit``/``offset``."""
    total = int(db.execute(select(func.count()).select_from(Podcast)).scalar_one())
    statement = (
        select(Podcast)
        .order_by(Podcast.name)
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = list(db.execute(statement).scalars().all())
    return Page[PodcastRead](
        items=[PodcastRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_podcast(podcast_id: int, db: DbSession) -> Podcast:
    """Return a single podcast source by id."""
    podcast = db.get(Podcast, podcast_id)
    if podcast is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    return podcast


router.add_api_route(
    "",
    list_podcasts,
    methods=["GET"],
    response_model=Page[PodcastRead],
)
router.add_api_route(
    "/{podcast_id}",
    get_podcast,
    methods=["GET"],
    response_model=PodcastRead,
)
