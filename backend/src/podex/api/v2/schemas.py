"""Schemas for the version 2 API surface."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from podex.models.audit_log import AuditAction
from podex.models.media import MediaType
from podex.models.media_external_ref import MediaExternalRefSource
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
from podex.models.takedown_request import (
    TakedownRequesterType,
    TakedownRequestStatus,
    TakedownSubjectType,
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


class AuthMagicLinkRequest(BaseModel):
    """Request a passwordless email sign-in challenge."""

    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    redirect_path: str | None = Field(default=None, max_length=300)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """Normalize textual email input before format validation."""
        return value.strip().casefold() if isinstance(value, str) else value

    @field_validator("redirect_path")
    @classmethod
    def validate_redirect_path(cls, value: str | None) -> str | None:
        """Restrict sign-in continuation destinations to local paths."""
        if value is not None and (not value.startswith("/") or value.startswith("//")):
            raise ValueError("redirect_path must be a local absolute path")
        return value


class AuthMagicLinkRequestResponse(BaseModel):
    """Generic acknowledgement for sign-in link delivery requests."""

    accepted: bool = True


class AccountUserResponse(BaseModel):
    """Authenticated user's minimal account identity."""

    id: str
    email: str
    created_at: datetime
    last_signed_in_at: datetime | None = None


class AuthMagicLinkVerifyRequest(BaseModel):
    """One-time token received from an emailed sign-in link."""

    token: str = Field(min_length=20, max_length=200)


class AuthSessionResponse(BaseModel):
    """New authenticated browser session response."""

    user: AccountUserResponse
    expires_at: datetime


class AuthLogoutResponse(BaseModel):
    """Confirmation that the current browser session was revoked."""

    signed_out: bool


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


class AccountSavedMediaResponse(BaseModel):
    """A public catalog media record saved by the authenticated user."""

    media: PublicMediaSummary
    saved_at: datetime


class AccountSavedMediaListResponse(BaseModel):
    """Saved public catalog media belonging to the authenticated user."""

    items: list[AccountSavedMediaResponse]
    total: int


class AccountSavedMediaDeleteResponse(BaseModel):
    """Confirmation that a media record is no longer saved."""

    deleted: bool


class AccountFollowedPodcastResponse(BaseModel):
    """A public podcast source followed by the authenticated user."""

    podcast: PublicPodcastSummary
    followed_at: datetime


class AccountFollowedPodcastListResponse(BaseModel):
    """Followed public podcast sources belonging to the authenticated user."""

    items: list[AccountFollowedPodcastResponse]
    total: int


class AccountFollowedPodcastDeleteResponse(BaseModel):
    """Confirmation that a podcast source is no longer followed."""

    deleted: bool


class AccountAlertRuleCreateRequest(BaseModel):
    """Create an alert rule over a saved or followed public resource."""

    target_type: Literal["media", "podcast"]
    target_id: str
    event_type: Literal["new_mention", "new_episode"]


class AccountAlertRuleUpdateRequest(BaseModel):
    """Update mutable alert rule preferences."""

    enabled: bool


class AccountAlertRuleResponse(BaseModel):
    """Authenticated alert rule boundary payload."""

    id: str
    target_type: Literal["media", "podcast"]
    target_id: str
    event_type: Literal["new_mention", "new_episode"]
    baseline_count: int
    enabled: bool
    last_evaluated_at: datetime | None = None
    created_at: datetime


class AccountAlertRuleListResponse(BaseModel):
    """All alert rules configured for the authenticated user."""

    items: list[AccountAlertRuleResponse]
    total: int


class AccountAlertRuleDeleteResponse(BaseModel):
    """Confirmation that an alert rule was removed."""

    deleted: bool


class AccountAlertEventResponse(BaseModel):
    """A generated alert event following rule evaluation."""

    id: int
    rule: AccountAlertRuleResponse
    previous_count: int
    observed_count: int
    created_at: datetime


class AccountAlertEvaluationResponse(BaseModel):
    """Alert events generated during an evaluation run."""

    items: list[AccountAlertEventResponse]
    generated: int


class AccountDigestResponse(BaseModel):
    """A delivered digest of generated alert events."""

    id: str
    channel: str
    subject: str
    body_text: str
    event_count: int
    created_at: datetime
    delivered_at: datetime | None = None


class AccountDigestListResponse(BaseModel):
    """Delivered notification digests for the current account."""

    items: list[AccountDigestResponse]
    total: int


class AccountDigestSendResponse(BaseModel):
    """Result of evaluating events and attempting a digest delivery."""

    digest: AccountDigestResponse | None = None
    delivered: bool


class AccountPreferenceUpdateRequest(BaseModel):
    """User-controlled notification delivery settings."""

    digest_enabled: bool
    digest_frequency: Literal["immediate", "daily", "weekly"]


class AccountPreferenceResponse(BaseModel):
    """Persisted account notification settings."""

    digest_enabled: bool
    digest_frequency: Literal["immediate", "daily", "weekly"]
    updated_at: datetime


class AccountQuotaResponse(BaseModel):
    """Monthly entitlement consumption for an account feature."""

    period: str
    feature: str
    limit: int
    used: int
    remaining: int


class AccountSubscriptionResponse(BaseModel):
    """Hosted plan entitlement and quota state for an account."""

    tier: Literal["free", "paid"]
    status: str
    paid_access: bool
    paid_tier_enabled: bool
    paid_features_enforced: bool
    quotas: list[AccountQuotaResponse]
    current_period_ends_at: datetime | None = None


class AccountSubscriptionCheckoutResponse(BaseModel):
    """Hosted billing checkout destination for a paid upgrade."""

    provider: str
    checkout_url: str


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


class PublicRelatedMedia(BaseModel):
    """Related catalog record with the provenance for its public relation."""

    id: str
    type: MediaType
    title: str
    author: str | None = None
    cover_url: str | None = None
    relation_type: str
    direction: Literal["outgoing", "incoming"]
    source: str
    confidence: float
    evidence_text: str | None = None
    provenance_episode_id: str | None = None


class PublicMediaDetail(PublicMediaSummary):
    """Detailed public media payload for the v2 API."""

    mentions: list[PublicMentionOccurrence] = []
    derivative_summary: str | None = None
    related_media: list[PublicRelatedMedia] = Field(default_factory=list)
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
    derivative_summary: str | None = None
    derivative_mentioned_media_titles: list[str] = Field(default_factory=list)
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


class PublicSearchSelectionRequest(BaseModel):
    """Anonymous selected search result submitted for relevance analysis."""

    query: str = Field(min_length=1, max_length=200)
    result_type: Literal["media", "episode", "podcast"]
    result_id: str = Field(min_length=1, max_length=120)


class PublicSearchSelectionResponse(BaseModel):
    """Acknowledgement for an anonymous search relevance signal."""

    recorded: bool


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


class PublicEditorialCollectionSummary(BaseModel):
    """Published editorial collection available for public discovery."""

    slug: str
    title: str
    description: str
    curator_name: str | None = None
    featured: bool
    item_count: int
    updated_at: datetime


class PublicEditorialCollectionDetail(PublicEditorialCollectionSummary):
    """Published collection with ordered catalog references."""

    items: list[PublicMediaSummary]


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


class OpsReviewThroughputMetrics(BaseModel):
    """Review queue throughput and backlog health."""

    pending_items: int
    decisions_last_24h: int
    median_decision_minutes_last_24h: float | None = None


class OpsProjectionLagMetrics(BaseModel):
    """Search projection repair backlog health."""

    pending_repairs: int
    failed_repairs: int
    oldest_pending_age_seconds: int | None = None


class OpsAlertDeliveryMetrics(BaseModel):
    """Account notification generation and delivery health."""

    generated_events_last_24h: int
    delivered_digests_last_24h: int
    delivered_events_last_24h: int
    pending_events: int


class OpsOperationalMetricsResponse(BaseModel):
    """Stabilization metrics exposed to operators."""

    measured_at: datetime
    review: OpsReviewThroughputMetrics
    projection: OpsProjectionLagMetrics
    alerts: OpsAlertDeliveryMetrics


class OpsOperationalAlert(BaseModel):
    """An actionable threshold breach shown to operators."""

    key: str
    severity: Literal["warning", "critical"]
    title: str
    message: str
    current_value: int
    threshold: int
    playbook_slug: str


class OpsOperationalAlertResponse(BaseModel):
    """Current actionable operational threshold breaches."""

    measured_at: datetime
    alerts: list[OpsOperationalAlert]


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


class OpsMediaAlias(BaseModel):
    """Normalized alias attached to a canonical media record."""

    alias: str
    normalized_alias: str
    source: str
    is_primary: bool


class OpsMediaExternalRef(BaseModel):
    """External reference attached to a canonical media record."""

    source: str
    external_id: str
    url: str | None = None
    label: str | None = None
    description: str | None = None


class OpsMediaRelation(BaseModel):
    """Typed relationship involving a canonical media record."""

    direction: str
    relation_type: str
    related_media: OpsMergedMediaSummary
    source: str
    confidence: float


class OpsMediaMention(BaseModel):
    """Published mention selectable for a canonical media split."""

    id: str
    episode_id: str
    episode_title: str
    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float


class OpsMediaDetailResponse(BaseModel):
    """Editable canonical media detail for the ops console."""

    media: OpsMergedMediaSummary
    google_books_id: str | None = None
    open_library_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    wikipedia_id: str | None = None
    pubmed_id: str | None = None
    doi: str | None = None
    semantic_scholar_id: str | None = None
    metadata_json: dict[str, Any] | None = None
    verification_sources: list[str] = Field(default_factory=list)
    aliases: list[OpsMediaAlias] = Field(default_factory=list)
    external_refs: list[OpsMediaExternalRef] = Field(default_factory=list)
    relations: list[OpsMediaRelation] = Field(default_factory=list)
    mentions: list[OpsMediaMention] = Field(default_factory=list)


class OpsMediaUpdateRequest(BaseModel):
    """Partial operator correction for canonical media metadata."""

    type: MediaType | None = None
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=255)
    cover_url: str | None = Field(default=None, max_length=500)
    year: int | None = None
    description: str | None = None
    google_books_id: str | None = Field(default=None, max_length=50)
    open_library_id: str | None = Field(default=None, max_length=50)
    imdb_id: str | None = Field(default=None, max_length=20)
    tmdb_id: int | None = None
    wikipedia_id: str | None = Field(default=None, max_length=100)
    pubmed_id: str | None = Field(default=None, max_length=50)
    doi: str | None = Field(default=None, max_length=100)
    semantic_scholar_id: str | None = Field(default=None, max_length=50)
    metadata_json: dict[str, Any] | None = None
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = None


class OpsMediaAliasRequest(BaseModel):
    """Payload for adding an operator-managed canonical alias."""

    alias: str = Field(min_length=1, max_length=500)
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = None


class OpsMediaExternalRefRequest(BaseModel):
    """Payload for upserting an operator-managed external reference."""

    source: MediaExternalRefSource
    external_id: str = Field(min_length=1, max_length=255)
    url: str | None = Field(default=None, max_length=500)
    label: str | None = Field(default=None, max_length=255)
    description: str | None = None
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = None


class OpsMediaSplitRequest(BaseModel):
    """Payload for recovering mentions from an incorrectly merged record."""

    mention_ids: list[str] = Field(min_length=1)
    type: MediaType
    title: str = Field(min_length=1, max_length=500)
    author: str | None = Field(default=None, max_length=255)
    description: str | None = None
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = None


class OpsMediaSplitResponse(BaseModel):
    """Updated source and new record created during split recovery."""

    source: OpsMediaDetailResponse
    created: OpsMediaDetailResponse
    mentions_moved: int


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


class OpsReviewSplitCandidateInput(BaseModel):
    """Replacement candidate definition for a split review decision."""

    type: MediaType
    raw_title: str = Field(min_length=1, max_length=500)
    suggested_author: str | None = Field(default=None, max_length=255)


class OpsReviewSplitRequest(OpsReviewDecisionRequest):
    """Payload for splitting an ambiguous candidate into review items."""

    candidates: list[OpsReviewSplitCandidateInput] = Field(min_length=2)


class OpsReviewSplitResponse(BaseModel):
    """Original split item and queued replacement review items."""

    original: OpsReviewQueueItem
    items: list[OpsReviewQueueItem]


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


class OpsSearchReindexRequest(BaseModel):
    """Filters for manually queuing projection rebuild work."""

    resource_type: Literal["all", "media", "episode"] = "all"
    podcast_id: str | None = None
    media_type: MediaType | None = None
    created_after: datetime | None = None
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class OpsSearchReindexResponse(BaseModel):
    """Summary of a manually queued projection rebuild."""

    media_queued: int
    episodes_queued: int
    total_queued: int


class OpsSearchTuningPreviewRequest(BaseModel):
    """Candidate synonym and ranking configuration for an index."""

    index: Literal["media", "episodes", "podcasts"]
    query: str = Field(min_length=1, max_length=200)
    synonyms: dict[str, list[str]] = Field(default_factory=dict)
    ranking_rules: list[str] | None = None


class OpsSearchTuningPreviewResponse(BaseModel):
    """Baseline query results and proposed index configuration."""

    index: str
    query: str
    sample_hits: list[dict[str, Any]]
    proposed_settings: dict[str, Any]


class OpsSearchTuningApplyRequest(OpsSearchTuningPreviewRequest):
    """Reviewed search tuning changes to apply."""

    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class OpsSearchTuningApplyResponse(BaseModel):
    """Result returned after enqueuing tuning settings."""

    index: str
    status: str
    task_uid: int | None = None


class OpsSearchQueryMetric(BaseModel):
    """Per-query relevance signal aggregation."""

    query: str
    searches: int
    zero_result_searches: int
    selections: int


class OpsSearchAnalyticsResponse(BaseModel):
    """Anonymous public-search signals used to guide tuning."""

    searches: int
    zero_result_searches: int
    selections: int
    queries: list[OpsSearchQueryMetric]


class OpsRetentionSamplingStratum(BaseModel):
    """Coverage metrics for one retention sampling stratum."""

    source: str
    topic: str
    confidence_band: str
    age_bucket: str
    eligible_count: int
    sampled_count: int
    target_count: int


class OpsRetentionSamplingReportResponse(BaseModel):
    """Coverage report for the permanent calibration corpus."""

    policy_version: str
    sample_rate: float
    eligible_count: int
    sampled_count: int
    target_count: int
    strata: list[OpsRetentionSamplingStratum]


class OpsRetentionSamplingRecalculateRequest(BaseModel):
    """Versioned retention sampling policy to apply."""

    policy_version: str = Field(min_length=1, max_length=80)
    sample_rate: float = Field(ge=0.05, le=0.1)
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class OpsTranscriptRetentionPolicyRequest(BaseModel):
    """Lifecycle policy values used for a transcript preview or evaluation."""

    policy_version: str = Field(min_length=1, max_length=80)
    hot_days: int = Field(default=30, ge=0, le=3650)
    warm_days: int = Field(default=180, ge=0, le=3650)
    min_purge_confidence: float = Field(default=0.85, ge=0, le=1)
    source_retention_opt_out: bool = False
    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class OpsTranscriptRetentionSummary(BaseModel):
    """Current raw transcript lifecycle state exposed to operators."""

    id: str
    episode_id: str
    episode_title: str
    podcast_name: str
    provider: str
    fetched_at: datetime | None = None
    tier: str
    policy_version: str | None = None
    retention_exempt_sample: bool
    source_retention_opt_out: bool
    purge_eligible_at: datetime | None = None
    purged_at: datetime | None = None
    has_raw_payload: bool
    has_stored_artifact: bool
    digest_id: str | None = None


class OpsTranscriptRetentionListResponse(BaseModel):
    """Recent transcript assets exposed for lifecycle management."""

    items: list[OpsTranscriptRetentionSummary]


class OpsTranscriptRetentionPreviewResponse(BaseModel):
    """Dry-run or persisted lifecycle evaluation and purge gate state."""

    transcript: OpsTranscriptRetentionSummary
    proposed_tier: str
    purge_eligible: bool
    purge_blockers: list[str]
    extraction_confidence: float | None = None
    derivative_coverage_ready: bool
    missing_query_classes: list[str]


class OpsTranscriptDigestResponse(BaseModel):
    """Proof-of-processing retained after raw transcript deletion."""

    id: str
    transcript_id: str
    source_text_hash: str
    provider: str
    policy_version: str | None = None
    summary_text: str
    extraction_versions: list[str]
    purged_at: datetime


class OpsTranscriptPurgeResponse(BaseModel):
    """Lifecycle and proof state after a successful raw-transcript purge."""

    transcript: OpsTranscriptRetentionSummary
    digest: OpsTranscriptDigestResponse


class OpsTranscriptReacquireRequest(BaseModel):
    """Operator attribution for raw transcript re-acquisition."""

    actor_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)


class OpsTranscriptReacquireResponse(BaseModel):
    """Fresh hot transcript artifact created from a preserved digest."""

    transcript: OpsTranscriptRetentionSummary
    artifact_id: str
    prior_digest_id: str


class PublicTakedownRequestCreateRequest(BaseModel):
    """Rights-holder or creator request for privileged content review."""

    subject_type: TakedownSubjectType
    subject_id: str
    requester_type: TakedownRequesterType
    requester_name: str = Field(min_length=1, max_length=255)
    requester_email: str = Field(min_length=3, max_length=320)
    basis: str = Field(min_length=10, max_length=5000)
    requested_actions: list[
        Literal[
            "suppress_raw_transcript",
            "suppress_derivatives",
            "unpublish_mentions",
            "purge_search_projection",
            "register_source_opt_out",
        ]
    ] = Field(min_length=1)


class PublicTakedownRequestCreateResponse(BaseModel):
    """Acknowledgement returned for a public takedown submission."""

    id: str
    status: TakedownRequestStatus
    submitted_at: datetime


class OpsTakedownRequestSummary(BaseModel):
    """Privileged view of a submitted takedown request."""

    id: str
    subject_type: TakedownSubjectType
    subject_id: str
    requester_type: TakedownRequesterType
    requester_name: str
    requester_email: str
    basis: str
    requested_actions: list[str]
    status: TakedownRequestStatus
    decision_note: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    submitted_at: datetime


class OpsTakedownRequestListResponse(BaseModel):
    """Pending or historical takedown cases for operator review."""

    items: list[OpsTakedownRequestSummary]


class OpsTakedownDecisionRequest(BaseModel):
    """Reviewed takedown outcome, executing requested actions on approval."""

    status: Literal["approved", "rejected"]
    actor_name: str | None = Field(default=None, max_length=100)
    note: str = Field(min_length=1, max_length=1000)


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
