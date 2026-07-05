"""Public read endpoints for podcast sources."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from podex.api.deps import DbSession
from podex.models import Podcast
from podex.schemas.podcast import PodcastRead

router = APIRouter(prefix="/podcasts", tags=["podcasts"])


def list_podcasts(db: DbSession) -> list[Podcast]:
    """List podcast sources ordered by name."""
    result = db.execute(select(Podcast).order_by(Podcast.name))
    return list(result.scalars().all())


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
    response_model=list[PodcastRead],
)
router.add_api_route(
    "/{podcast_id}",
    get_podcast,
    methods=["GET"],
    response_model=PodcastRead,
)
