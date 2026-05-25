"""Operator commands for maintaining the search projection."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Query, Session

from podex.models import Episode, Media, Mention
from podex.models.media import MediaType
from podex.models.search_projection_repair import (
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
)
from podex.services.search_projection_repairs import ensure_search_projection_repair


@dataclass(frozen=True, slots=True)
class OpsSearchReindexInputData:
    """Filters for a manually requested reindex repair batch."""

    resource_type: str
    podcast_id: int | None
    media_type: MediaType | None
    created_after: datetime | None


@dataclass(frozen=True, slots=True)
class OpsSearchReindexResultData:
    """Counts for resources queued by a manually requested reindex."""

    media_queued: int
    episodes_queued: int

    @property
    def total_queued(self) -> int:
        """Return total resources queued for repair."""
        return self.media_queued + self.episodes_queued


def queue_ops_search_reindex(
    *,
    db: Session,
    payload: OpsSearchReindexInputData,
) -> OpsSearchReindexResultData:
    """Queue replay-safe projection repairs narrowed by operator filters."""
    media_queued = 0
    episodes_queued = 0

    if payload.resource_type in {"all", "media"}:
        media_query: Query[Media] = db.query(Media)
        if payload.podcast_id is not None:
            media_query = (
                media_query.join(Mention, Mention.media_id == Media.id)
                .join(Episode, Episode.id == Mention.episode_id)
                .filter(Episode.podcast_id == payload.podcast_id)
            )
        if payload.media_type is not None:
            media_query = media_query.filter(Media.type == payload.media_type.value)
        if payload.created_after is not None:
            media_query = media_query.filter(Media.created_at >= payload.created_after)

        for (media_id,) in media_query.with_entities(Media.id).distinct().all():
            ensure_search_projection_repair(
                db=db,
                resource_type=SearchProjectionRepairResourceType.MEDIA,
                resource_id=media_id,
                reason=SearchProjectionRepairReason.MANUAL_REINDEX,
                status=SearchProjectionRepairStatus.PENDING,
                metadata_json={"operator_requested": True},
            )
            media_queued += 1

    if payload.resource_type in {"all", "episode"}:
        episode_query: Query[Episode] = db.query(Episode)
        if payload.podcast_id is not None:
            episode_query = episode_query.filter(
                Episode.podcast_id == payload.podcast_id
            )
        if payload.media_type is not None:
            episode_query = (
                episode_query.join(Mention, Mention.episode_id == Episode.id)
                .join(Media, Media.id == Mention.media_id)
                .filter(Media.type == payload.media_type.value)
            )
        if payload.created_after is not None:
            episode_query = episode_query.filter(
                Episode.created_at >= payload.created_after
            )

        for (episode_id,) in episode_query.with_entities(Episode.id).distinct().all():
            ensure_search_projection_repair(
                db=db,
                resource_type=SearchProjectionRepairResourceType.EPISODE,
                resource_id=episode_id,
                reason=SearchProjectionRepairReason.MANUAL_REINDEX,
                status=SearchProjectionRepairStatus.PENDING,
                metadata_json={"operator_requested": True},
            )
            episodes_queued += 1

    db.flush()
    return OpsSearchReindexResultData(
        media_queued=media_queued,
        episodes_queued=episodes_queued,
    )
