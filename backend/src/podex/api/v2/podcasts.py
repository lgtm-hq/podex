"""Public read endpoints for podcast sources.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.podcast_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException
<<<<<<< HEAD
from sqlalchemy import func, select
=======
>>>>>>> origin/main

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Podcast
from podex.schemas.podcast import PodcastRead
from podex.services import podcast_queries

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def list_podcasts(db: DbSession, pagination: Pagination) -> Page[PodcastRead]:
    """List podcast sources ordered by name, paginated by ``limit``/``offset``."""
<<<<<<< HEAD
    total = int(db.execute(select(func.count()).select_from(Podcast)).scalar_one())
    statement = (
        select(Podcast)
        .order_by(Podcast.name)
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = list(db.execute(statement).scalars().all())
=======
    total = podcast_queries.count_podcasts(db)
    rows = podcast_queries.list_podcasts(
        db,
        limit=pagination.limit,
        offset=pagination.offset,
    )
>>>>>>> origin/main
    return Page[PodcastRead](
        items=[PodcastRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_podcast(podcast_id: int, db: DbSession) -> Podcast:
    """Return a single podcast source by id."""
    podcast = podcast_queries.get_podcast(db, podcast_id)
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
