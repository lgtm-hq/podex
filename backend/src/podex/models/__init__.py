"""ORM models package.

Importing the models here registers them on ``Base.metadata`` so that
metadata-based table creation and Alembic autogenerate see every table.
"""

from podex.models.account_followed_podcast import AccountFollowedPodcast
from podex.models.account_preference import AccountPreference
from podex.models.account_saved_media import AccountSavedMedia
from podex.models.account_user import AccountUser
from podex.models.base import Base
from podex.models.derivative_generation_run import (
    DerivativeGenerationRun,
    DerivativeGenerationRunStatus,
)
from podex.models.derivative_summary import (
    DerivativeSummaryKind,
    DerivativeSummaryStatus,
)
from podex.models.episode import DiscoverySource, Episode
from podex.models.episode_summary import EpisodeSummary
from podex.models.graph_triple import GraphTriple, GraphTripleObjectKind
from podex.models.ingestion_run import IngestionRun, IngestionRunStatus
from podex.models.magic_link_token import MagicLinkToken
from podex.models.media import Media, MediaType
from podex.models.media_alias import MediaAlias, MediaAliasSourceType
from podex.models.media_external_ref import MediaExternalRef, MediaExternalRefSource
from podex.models.media_relation import MediaRelation, MediaRelationType
from podex.models.media_summary import MediaSummary
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
from podex.models.semantic_chunk import (
    SemanticChunk,
    SemanticChunkEmbeddingStatus,
)
from podex.models.transcript import Transcript
from podex.models.transcript_artifact import TranscriptArtifact
from podex.models.transcript_digest import TranscriptDigest
from podex.models.transcript_source_retention_policy import (
    TranscriptSourceRetentionPolicy,
)
from podex.models.user_session import UserSession

__all__ = [
    "AccountFollowedPodcast",
    "AccountPreference",
    "AccountSavedMedia",
    "AccountUser",
    "Base",
    "DerivativeGenerationRun",
    "DerivativeGenerationRunStatus",
    "DerivativeSummaryKind",
    "DerivativeSummaryStatus",
    "EpisodeSummary",
    "GraphTriple",
    "GraphTripleObjectKind",
    "MagicLinkToken",
    "MediaSummary",
    "SemanticChunk",
    "SemanticChunkEmbeddingStatus",
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
    "TranscriptArtifact",
    "TranscriptDigest",
    "TranscriptSourceRetentionPolicy",
    "ScheduledWorkStatus",
    "UserSession",
]
