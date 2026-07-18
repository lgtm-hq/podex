"""Public read endpoints for podcast episodes.

Route handlers here are intentionally thin: they validate inputs via FastAPI,
delegate the query to :mod:`podex.services.episode_queries`, and map missing
rows to HTTP 404 responses.
"""

from fastapi import APIRouter, HTTPException

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Episode
from podex.schemas.episode import EpisodeRead
from podex.schemas.mention import MentionRead
from podex.services import episode_queries

router = APIRouter(prefix="/episodes", tags=["episodes"])


def list_episodes(
    db: DbSession,
    pagination: Pagination,
    podcast_id: int | None = None,
) -> Page[EpisodeRead]:
    """List episodes, optionally filtered by podcast, paginated."""
    total = episode_queries.count_episodes(db, podcast_id=podcast_id)
    rows = episode_queries.list_episodes(
        db,
        podcast_id=podcast_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    return Page[EpisodeRead](
        items=[EpisodeRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_episode(episode_id: int, db: DbSession) -> Episode:
    """Return a single episode by id."""
    episode = episode_queries.get_episode(db, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


def list_episode_mentions(
    episode_id: int,
    db: DbSession,
    pagination: Pagination,
) -> Page[MentionRead]:
    """List media mentions within an episode, ordered by timestamp, paginated."""
    rows = episode_queries.list_episode_mentions(
        db,
        episode_id,
        limit=pagination.limit,
        offset=pagination.offset,
    )
    if rows is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    total = episode_queries.count_episode_mentions(db, episode_id)
    return Page[MentionRead](
        items=[MentionRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


router.add_api_route(
    "",
    list_episodes,
    methods=["GET"],
    response_model=Page[EpisodeRead],
)
router.add_api_route(
    "/{episode_id}/mentions",
    list_episode_mentions,
    methods=["GET"],
    response_model=Page[MentionRead],
)
router.add_api_route(
    "/{episode_id}",
    get_episode,
    methods=["GET"],
    response_model=EpisodeRead,
)
