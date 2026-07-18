"""Public read endpoints for podcast sources.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.podcast_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException

from podex.api.deps import DbSession
from podex.models import Podcast
from podex.schemas.podcast import PodcastRead
from podex.services import podcast_queries

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def list_podcasts(db: DbSession) -> list[Podcast]:
    """List podcast sources ordered by name."""
    return podcast_queries.list_podcasts(db)


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
    response_model=list[PodcastRead],
)
router.add_api_route(
    "/{podcast_id}",
    get_podcast,
    methods=["GET"],
    response_model=PodcastRead,
)
