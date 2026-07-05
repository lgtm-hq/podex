"""Public read endpoints for podcast episodes."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from podex.api.deps import DbSession
from podex.models import Episode
from podex.schemas.episode import EpisodeRead

router = APIRouter(prefix="/episodes", tags=["episodes"])


def list_episodes(db: DbSession, podcast_id: int | None = None) -> list[Episode]:
    """List episodes, optionally filtered by podcast."""
    statement = select(Episode).order_by(Episode.published_at.desc())
    if podcast_id is not None:
        statement = statement.where(Episode.podcast_id == podcast_id)
    return list(db.execute(statement).scalars().all())


def get_episode(episode_id: int, db: DbSession) -> Episode:
    """Return a single episode by id."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


router.add_api_route(
    "",
    list_episodes,
    methods=["GET"],
    response_model=list[EpisodeRead],
)
router.add_api_route(
    "/{episode_id}",
    get_episode,
    methods=["GET"],
    response_model=EpisodeRead,
)
