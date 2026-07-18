"""Public read endpoints for podcast episodes."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from podex.api.deps import DbSession, Pagination
from podex.api.v2.schemas import Page
from podex.models import Episode, Mention
from podex.schemas.episode import EpisodeRead
from podex.schemas.mention import MentionRead

router = APIRouter(prefix="/episodes", tags=["episodes"])


def list_episodes(
    db: DbSession,
    pagination: Pagination,
    podcast_id: int | None = None,
) -> Page[EpisodeRead]:
    """List episodes, optionally filtered by podcast, paginated."""
    count_stmt = select(func.count()).select_from(Episode)
    statement = select(Episode).order_by(Episode.published_at.desc())
    if podcast_id is not None:
        count_stmt = count_stmt.where(Episode.podcast_id == podcast_id)
        statement = statement.where(Episode.podcast_id == podcast_id)
    total = int(db.execute(count_stmt).scalar_one())
    statement = statement.offset(pagination.offset).limit(pagination.limit)
    rows = list(db.execute(statement).scalars().all())
    return Page[EpisodeRead](
        items=[EpisodeRead.model_validate(row) for row in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


def get_episode(episode_id: int, db: DbSession) -> Episode:
    """Return a single episode by id."""
    episode = db.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    return episode


def list_episode_mentions(
    episode_id: int,
    db: DbSession,
    pagination: Pagination,
) -> Page[MentionRead]:
    """List media mentions within an episode, ordered by timestamp, paginated."""
    if db.get(Episode, episode_id) is None:
        raise HTTPException(status_code=404, detail="Episode not found")
    total = int(
        db.execute(
            select(func.count())
            .select_from(Mention)
            .where(Mention.episode_id == episode_id),
        ).scalar_one(),
    )
    statement = (
        select(Mention)
        .where(Mention.episode_id == episode_id)
        .order_by(Mention.timestamp_seconds)
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
