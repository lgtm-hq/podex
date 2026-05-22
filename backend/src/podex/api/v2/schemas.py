"""Schemas for the version 2 API surface."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from podex.models.audit_log import AuditAction
from podex.models.media import MediaType
from podex.models.mention_candidate import MentionCandidateState
from podex.models.mention_candidate_provenance import (
    MentionCandidateProvenanceEventType,
)
from podex.models.podcast import PodcastStatus
from podex.models.review_item import ReviewItemStatus, ReviewPriority
from podex.models.search_projection_repair import (
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
)
from podex.models.transcription_job import JobStatus, JobType


class PublicPodcastSummary(BaseModel):
    """Public podcast summary for the v2 API."""

    id: str
    name: str
    slug: str
    description: str | None = None
    cover_url: str | None = None
    created_at: datetime
    episode_count: int = 0
    mention_count: int = 0


class PublicPodcastDetail(PublicPodcastSummary):
    """Detailed public podcast payload for the v2 API."""


class PublicEpisodeSummary(BaseModel):
    """Public episode summary for the v2 API."""

    id: str
    podcast_id: str
    title: str
    episode_number: int | None = None
    youtube_id: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    thumbnail_url: str | None = None
    transcript_status: str
    created_at: datetime
    mention_count: int = 0


class PublicEpisodeReference(BaseModel):
    """Compact episode reference for nested public payloads."""

    id: str
    title: str
    episode_number: int | None = None
    youtube_id: str | None = None
    published_at: datetime | None = None
    thumbnail_url: str | None = None


class PublicMentionOccurrence(BaseModel):
    """Mention occurrence exposed on the public v2 API."""

    id: str
    episode: PublicEpisodeReference
    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float = 1.0
    youtube_timestamp_url: str | None = None


class PublicMediaSummary(BaseModel):
    """Public media summary for the v2 API."""

    id: str
    type: MediaType
    title: str
    author: str | None = None
    cover_url: str | None = None
    year: int | None = None
    description: str | None = None
    mention_count: int = 0
    episode_count: int = 0
    created_at: datetime


class PublicMediaReference(BaseModel):
    """Compact media reference for nested public episode payloads."""

    id: str
    type: str
    title: str
    author: str | None = None


class PublicEpisodeMediaMention(BaseModel):
    """Media mention nested under a public episode payload."""

    id: str
    media: PublicMediaReference
    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float = 1.0
    youtube_timestamp_url: str | None = None


class PublicMediaDetail(PublicMediaSummary):
    """Detailed public media payload for the v2 API."""

    mentions: list[PublicMentionOccurrence] = []
    google_books_id: str | None = None
    open_library_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    wikipedia_id: str | None = None
    doi: str | None = None
    pubmed_id: str | None = None
    semantic_scholar_id: str | None = None
    metadata_json: dict[str, Any] | None = None
    enriched_at: datetime | None = None
    enrichment_source: str | None = None
    verification_sources: list[str] = []
    doi_verified: bool = False
    imdb_url: str | None = None
    tmdb_url: str | None = None
    wikipedia_url: str | None = None
    google_books_url: str | None = None
    open_library_url: str | None = None
    doi_url: str | None = None
    pubmed_url: str | None = None
    semantic_scholar_url: str | None = None


class PublicEpisodeDetail(PublicEpisodeSummary):
    """Detailed public episode payload for the v2 API."""

    podcast_name: str
    podcast_slug: str
    extraction_status: str
    cleanup_status: str
    mentions: list[PublicEpisodeMediaMention] = []


class PublicMediaListResponse(BaseModel):
    """Paginated public media response for the v2 API."""

    items: list[PublicMediaSummary]
    total: int
    page: int
    per_page: int


class PublicEpisodeListResponse(BaseModel):
    """Paginated public episode response for the v2 API."""

    items: list[PublicEpisodeSummary]
    total: int
    page: int
    per_page: int


class PublicSearchResultItem(BaseModel):
    """Single search hit exposed by the public v2 API."""

    id: str
    type: Literal["media", "episode", "podcast"]
    title: str
    subtitle: str | None = None
    cover_url: str | None = None
    url: str


class PublicSearchResultGroup(BaseModel):
    """Grouped search results for a specific resource type."""

    type: Literal["media", "episode", "podcast"]
    hits: list[PublicSearchResultItem]
    total: int


class PublicGlobalSearchResponse(BaseModel):
    """Aggregated public v2 search response."""

    query: str
    results: list[PublicSearchResultGroup]
    processing_time_ms: int


class PublicTrendsOverview(BaseModel):
    """Aggregate discovery metrics for the public trends view."""

    total_podcasts: int
    total_episodes: int
    total_media: int
    total_mentions: int
    total_books: int
    total_movies: int


class PublicTrendsByTypeSummary(BaseModel):
    """Discovery counts grouped by media type for the public API."""

    type: MediaType
    count: int
    mention_count: int


class PublicTrendingMediaItem(BaseModel):
    """Top-mentioned media item exposed on the public trends view."""

    id: str
    type: MediaType
    title: str
    author: str | None = None
    mention_count: int


class PublicTrendsResponse(BaseModel):
    """Combined public discovery trends payload for the v2 API."""

    overview: PublicTrendsOverview
    by_type: list[PublicTrendsByTypeSummary]
    top_mentioned: list[PublicTrendingMediaItem]


class PodcastEpisodeListResponse(PublicEpisodeListResponse):
    """Paginated episode response for v2 podcast routes."""


class CatalogSummary(BaseModel):
    """High-level podcast catalog metrics for ops."""

    total_podcasts: int
    active_podcasts: int
    watchlist_podcasts: int
    paused_podcasts: int


class SourceCoverageSummary(BaseModel):
    """Source configuration coverage metrics for ops."""

    with_rss: int
    with_spotify: int
    with_podscripts: int
    with_youtube: int


class EpisodeProcessingSummary(BaseModel):
    """Episode processing metrics for ops."""

    total_known: int
    transcribed: int
    extracted: int


class PipelineSummary(BaseModel):
    """Pipeline and queue metrics for ops."""

    ingestion_runs_total: int
    ingestion_runs_in_progress: int
    ingestion_runs_failed: int
    ingestion_runs_completed: int
    transcription_jobs_pending: int
    transcription_jobs_failed: int
    transcription_jobs_in_progress: int
    projection_repairs_pending: int = 0
    projection_repairs_failed: int = 0


class OpsPodcastSourceSummary(BaseModel):
    """Source identifiers exposed for ops podcast management."""

    rss_url: str | None = None
    spotify_id: str | None = None
    apple_id: str | None = None
    youtube_channel_id: str | None = None
    podscripts_slug: str | None = None


class OpsPodcastSourceInput(BaseModel):
    """Source fields accepted for ops podcast mutations."""

    rss_url: str | None = None
    spotify_id: str | None = None
    apple_id: str | None = None
    youtube_channel_id: str | None = None
    podscripts_slug: str | None = None


class OpsPodcastSummary(BaseModel):
    """Podcast summary for the v2 ops catalog management view."""

    id: str
    name: str
    slug: str
    status: str
    description: str | None = None
    cover_url: str | None = None
    created_at: datetime
    discovery_source: str | None = None
    episode_count: int = 0
    mention_count: int = 0
    sources: OpsPodcastSourceSummary


class OpsPodcastListResponse(BaseModel):
    """Paginated ops podcast management response for the v2 API."""

    items: list[OpsPodcastSummary]
    total: int
    page: int
    per_page: int


class OpsPodcastCreateRequest(BaseModel):
    """Payload for creating a podcast via the v2 ops API."""

    name: str
    slug: str
    status: PodcastStatus = PodcastStatus.WATCHLIST
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInput = OpsPodcastSourceInput()


class OpsPodcastUpdateRequest(BaseModel):
    """Payload for partially updating a podcast via the v2 ops API."""

    name: str | None = None
    slug: str | None = None
    status: PodcastStatus | None = None
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInput | None = None


class OpsIngestionRunSummary(BaseModel):
    """Recent ingestion run summary for v2 ops payloads."""

    id: str
    status: str
    error_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    duration_seconds: int | None = None


class OpsTranscriptionJobSummary(BaseModel):
    """Recent transcription job summary for v2 ops payloads."""

    id: str
    episode_id: str
    podcast_id: str
    podcast_name: str
    podcast_slug: str
    episode_title: str
    job_type: str
    status: str
    backend: str | None = None
    model: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    duration_seconds: int | None = None


class OpsEpisodeRerunRequest(BaseModel):
    """Payload for rerunning episode processing jobs via the v2 ops API."""

    job_types: list[JobType] = Field(
        default_factory=lambda: [
            JobType.TRANSCRIBE,
            JobType.EXTRACT,
            JobType.CLEANUP,
        ],
        min_length=1,
    )


class OpsEpisodeRerunResponse(BaseModel):
    """Created rerun jobs for a single episode on the v2 ops API."""

    episode_id: str
    jobs: list[OpsTranscriptionJobSummary]


class OpsMergedMediaSummary(BaseModel):
    """Merged media summary for v2 ops mutation responses."""

    id: str
    type: MediaType
    title: str
    author: str | None = None
    cover_url: str | None = None
    year: int | None = None
    description: str | None = None
    mention_count: int = 0
    episode_count: int = 0


class OpsMediaMergeRequest(BaseModel):
    """Payload for merging one media record into another via the v2 ops API."""

    target_id: str


class OpsMediaMergeResponse(BaseModel):
    """Merge result for the v2 ops media merge endpoint."""

    source_id: str
    target: OpsMergedMediaSummary


class OpsMediaMergeFieldChange(BaseModel):
    """Field-level change preview for an ops media merge."""

    field: str
    source_value: Any
    target_value: Any
    merged_value: Any


class OpsMediaMergeAliasAddition(BaseModel):
    """Alias that would be added to the target by an ops media merge."""

    alias: str
    normalized_alias: str
    source: str


class OpsMediaMergePreviewResponse(BaseModel):
    """Non-mutating preview for an ops media merge."""

    source: OpsMergedMediaSummary
    target: OpsMergedMediaSummary
    field_changes: list[OpsMediaMergeFieldChange] = Field(default_factory=list)
    alias_additions: list[OpsMediaMergeAliasAddition] = Field(default_factory=list)
    mentions_to_move: int = 0


class OpsReviewQueueCandidateProvenance(BaseModel):
    """Historical provenance entry nested inside a review candidate."""

    id: str
    event_type: MentionCandidateProvenanceEventType
    change_summary: str | None = None
    raw_title: str
    normalized_title: str | None = None
    suggested_author: str | None = None
    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float
    extraction_source: str | None = None
    source_job_id: str | None = None
    source_job_status: str | None = None
    source_job_backend: str | None = None
    source_job_model: str | None = None
    source_job_created_at: datetime | None = None
    media_id: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    created_at: datetime


class OpsReviewQueueExtractionJob(BaseModel):
    """Recent extraction job entry nested inside a review candidate."""

    id: str
    status: JobStatus
    backend: str | None = None
    model: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    is_source_job: bool = False


class OpsReviewQueueCandidate(BaseModel):
    """Candidate payload nested inside review queue items."""

    id: str
    type: MediaType
    raw_title: str
    normalized_title: str | None = None
    suggested_author: str | None = None
    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float
    extraction_source: str | None = None
    source_job_id: str | None = None
    source_job_status: str | None = None
    source_job_backend: str | None = None
    source_job_model: str | None = None
    source_job_created_at: datetime | None = None
    state: MentionCandidateState
    media_id: str | None = None
    mention_id: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    extraction_jobs: list[OpsReviewQueueExtractionJob] = Field(default_factory=list)
    provenance: list[OpsReviewQueueCandidateProvenance] = Field(default_factory=list)


class OpsReviewQueueItem(BaseModel):
    """Review queue item exposed by the v2 ops API."""

    id: str
    status: ReviewItemStatus
    priority: ReviewPriority
    assigned_to: str | None = None
    decision_note: str | None = None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None
    episode_id: str
    episode_title: str
    podcast_id: str
    podcast_name: str
    podcast_slug: str
    target_media_id: str | None = None
    candidate: OpsReviewQueueCandidate


class OpsReviewQueueListResponse(BaseModel):
    """Paginated review queue response for the v2 ops API."""

    items: list[OpsReviewQueueItem]
    total: int
    page: int
    per_page: int


class OpsReviewDecisionRequest(BaseModel):
    """Payload for approving or rejecting a review queue item."""

    actor_name: str | None = None
    note: str | None = None


class OpsReviewReclassifyRequest(OpsReviewDecisionRequest):
    """Payload for reclassifying a pending review candidate."""

    type: MediaType | None = None
    raw_title: str | None = None
    normalized_title: str | None = None
    suggested_author: str | None = None


class OpsReviewMergeRequest(OpsReviewDecisionRequest):
    """Payload for merging a review candidate into an existing media record."""

    target_id: str


class OpsAuditLogEntry(BaseModel):
    """Single audit log entry exposed by the v2 ops API."""

    id: str
    action: AuditAction
    resource_type: str
    resource_id: str | None = None
    actor_name: str | None = None
    summary: str
    metadata_json: dict[str, Any] | None = None
    created_at: datetime


class OpsAuditLogListResponse(BaseModel):
    """Paginated audit log response for the v2 ops API."""

    items: list[OpsAuditLogEntry]
    total: int
    page: int
    per_page: int


class AdminSettingsResponse(BaseModel):
    """Runtime configuration snapshot for the v2 admin API."""

    app_name: str
    debug: bool
    api_key_enabled: bool
    rate_limit_per_minute: int
    cors_origins: list[str]
    youtube_channel_id: str
    meilisearch_enabled: bool
    meilisearch_url: str


class AdminSettingsUpdateRequest(BaseModel):
    """Partial runtime configuration update payload for the v2 admin API."""

    debug: bool | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    cors_origins: list[str] | None = None
    youtube_channel_id: str | None = None
    meilisearch_enabled: bool | None = None
    meilisearch_url: str | None = None


class OpsPipelineActivityResponse(BaseModel):
    """Detailed ops pipeline activity response for the v2 API."""

    summary: PipelineSummary
    runs: list[OpsIngestionRunSummary]
    jobs: list[OpsTranscriptionJobSummary]


class OpsPipelineScheduleSummary(BaseModel):
    """Recurring pipeline schedule exposed to ops views."""

    id: str
    schedule_key: str
    task_kind: str
    interval_minutes: int
    enabled: bool
    metadata: dict[str, object] | None = None
    last_scheduled_at: datetime | None = None
    next_due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class OpsScheduledWorkItemSummary(BaseModel):
    """Scheduled work item exposed to ops views."""

    id: str
    schedule_id: str
    ingestion_run_id: str | None = None
    schedule_key: str
    work_key: str
    task_kind: str
    status: str
    due_at: datetime
    interval_minutes: int
    metadata: dict[str, object] | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class OpsScheduledWorkResponse(BaseModel):
    """Scheduled pipeline work response for ops views."""

    schedules: list[OpsPipelineScheduleSummary]
    work_items: list[OpsScheduledWorkItemSummary]


class OpsSearchProjectionIndexSummary(BaseModel):
    """Search projection stats for a single index."""

    name: str
    document_count: int = 0
    is_indexing: bool = False


class OpsSearchProjectionRepairSummary(BaseModel):
    """Projection repair record exposed on the ops search surface."""

    id: str
    resource_type: SearchProjectionRepairResourceType
    resource_id: str
    status: SearchProjectionRepairStatus
    reason: SearchProjectionRepairReason
    source_job_id: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class OpsSearchProjectionRepairCounts(BaseModel):
    """Aggregate projection repair counts for ops search surfaces."""

    pending: int = 0
    failed: int = 0
    completed: int = 0


class OpsSearchProjectionResponse(BaseModel):
    """Detailed search projection response for the v2 ops API."""

    configured: bool
    healthy: bool
    indexes: list[OpsSearchProjectionIndexSummary]
    repair_summary: OpsSearchProjectionRepairCounts
    repairs: list[OpsSearchProjectionRepairSummary] = Field(default_factory=list)


class SearchProjectionSummary(BaseModel):
    """Search projection health for ops."""

    enabled: bool


class OpsDashboardResponse(BaseModel):
    """Aggregated ops dashboard response for the v2 API."""

    catalog: CatalogSummary
    sources: SourceCoverageSummary
    episodes: EpisodeProcessingSummary
    pipelines: PipelineSummary
    search: SearchProjectionSummary
