"""ORM models package.

Importing the models here registers them on ``Base.metadata`` so that
metadata-based table creation and Alembic autogenerate see every table.
"""

from podex.models.base import Base
from podex.models.episode import DiscoverySource, Episode
from podex.models.ingestion_run import IngestionRun, IngestionRunStatus
from podex.models.media import Media, MediaType
from podex.models.media_alias import MediaAlias, MediaAliasSourceType
from podex.models.media_external_ref import MediaExternalRef, MediaExternalRefSource
from podex.models.media_relation import MediaRelation, MediaRelationType
from podex.models.mention import Mention
from podex.models.mention_candidate import MentionCandidate, MentionCandidateState
from podex.models.mention_candidate_provenance import (
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
)
from podex.models.podcast import Podcast, PodcastStatus
from podex.models.review_item import ReviewItem, ReviewItemStatus, ReviewPriority
from podex.models.scheduled_work import (
    PipelineSchedule,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
)
from podex.models.transcript import Transcript

__all__ = [
    "Base",
    "DiscoverySource",
    "Episode",
    "IngestionRun",
    "IngestionRunStatus",
    "Media",
    "MediaAlias",
    "MediaAliasSourceType",
    "MediaExternalRef",
    "MediaExternalRefSource",
    "MediaRelation",
    "MediaRelationType",
    "MediaType",
    "Mention",
    "MentionCandidate",
    "MentionCandidateProvenance",
    "MentionCandidateProvenanceEventType",
    "MentionCandidateState",
    "PipelineSchedule",
    "Podcast",
    "PodcastStatus",
    "ReviewItem",
    "ReviewItemStatus",
    "ReviewPriority",
    "ScheduledWorkItemModel",
    "Transcript",
    "ScheduledWorkStatus",
]
