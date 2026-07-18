"""Public read endpoints for podcast episodes.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.episode_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException

from podex.api.deps import DbSession
from podex.models import Episode, Mention
from podex.schemas.episode import EpisodeRead
from podex.schemas.mention import MentionRead
from podex.services import episode_queries

router = APIRouter(prefix="/episodes", tags=["episodes"])


def list_episodes(db: DbSession, podcast_id: int | None = None) -> list[Episode]:
    """List episodes, optionally filtered by podcast."""
    # Explicit annotation keeps the return well-typed even when the CI lint
    # image type-checks without SQLAlchemy installed, where the service
    # call would otherwise resolve to bare ``Any``.
    episodes: list[Episode] = episode_queries.list_episodes(db, podcast_id=podcast_id)
    return episodes


def get_episode(episode_id: int, db: DbSession) -> Episode:
    """Return a single episode by id."""
    episode = episode_queries.get_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


def list_episode_mentions(episode_id: int, db: DbSession) -> list[Mention]:
    """List media mentions within an episode, ordered by timestamp."""
    # See ``list_episodes`` for why the annotation is spelled out here.
    mentions: list[Mention] | None = episode_queries.list_episode_mentions(
        db,
        episode_id,
    )
    if mentions is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return mentions


router.add_api_route(
    "",
    list_episodes,
    methods=["GET"],
    response_model=list[EpisodeRead],
)
router.add_api_route(
    "/{episode_id}/mentions",
    list_episode_mentions,
    methods=["GET"],
    response_model=list[MentionRead],
)
router.add_api_route(
    "/{episode_id}",
    get_episode,
    methods=["GET"],
    response_model=EpisodeRead,
)
