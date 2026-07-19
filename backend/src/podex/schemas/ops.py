"""Ops console API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from podex.models import PodcastStatus
from podex.services.ops_podcast_queries import PodcastSourceType


class OpsPodcastSourcesRead(BaseModel):
    """Source identifiers associated with a managed podcast."""

    model_config = ConfigDict(from_attributes=True)

    rss_url: str | None
    spotify_id: str | None
    apple_id: str | None
    youtube_channel_id: str | None
    podscripts_slug: str | None


class OpsPodcastRead(BaseModel):
    """Podcast summary for ops catalog management views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    status: PodcastStatus
    description: str | None
    cover_url: str | None
    created_at: datetime
    discovery_source: str | None
    episode_count: int
    mention_count: int
    sources: OpsPodcastSourcesRead


class OpsPodcastListRead(BaseModel):
    """Paginated podcast management payload."""

    items: list[OpsPodcastRead]
    total: int
    page: int
    per_page: int


class OpsPodcastSourcesInput(BaseModel):
    """Source identifiers accepted on podcast mutations."""

    rss_url: str | None = Field(default=None, max_length=500)
    spotify_id: str | None = Field(default=None, max_length=50)
    apple_id: str | None = Field(default=None, max_length=50)
    youtube_channel_id: str | None = Field(default=None, max_length=30)
    podscripts_slug: str | None = Field(default=None, max_length=100)


class OpsPodcastCreateRequest(BaseModel):
    """Create a managed podcast."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    status: PodcastStatus = PodcastStatus.WATCHLIST
    description: str | None = Field(default=None, max_length=2000)
    cover_url: str | None = Field(default=None, max_length=500)
    discovery_source: str | None = None
    sources: OpsPodcastSourcesInput = OpsPodcastSourcesInput()


class OpsPodcastUpdateRequest(BaseModel):
    """Partially update a managed podcast; only provided fields change."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    status: PodcastStatus | None = None
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourcesInput | None = None


class OpsIngestionRunRead(BaseModel):
    """Ingestion run summary for pipeline views."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    error_summary: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    duration_seconds: int | None


class OpsPipelineActivityRead(BaseModel):
    """Recent pipeline activity."""

    runs: list[OpsIngestionRunRead]


class OpsReviewThroughputRead(BaseModel):
    """Review decision activity and outstanding pressure."""

    model_config = ConfigDict(from_attributes=True)

    pending_items: int
    decisions_last_24h: int
    median_decision_minutes_last_24h: float | None


class OpsAlertDeliveryRead(BaseModel):
    """Generated and delivered account notification health."""

    model_config = ConfigDict(from_attributes=True)

    generated_events_last_24h: int
    delivered_digests_last_24h: int
    delivered_events_last_24h: int
    pending_events: int


class OpsMetricsRead(BaseModel):
    """Combined operational metrics for the ops dashboard."""

    measured_at: datetime
    review: OpsReviewThroughputRead
    alerts: OpsAlertDeliveryRead


class OpsOperationalAlertRead(BaseModel):
    """One actionable operator-facing health alert."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    severity: str
    title: str
    message: str
    current_value: int
    threshold: int
    playbook_slug: str


class OpsOperationalAlertListRead(BaseModel):
    """Threshold breaches measured from operational metrics."""

    measured_at: datetime
    alerts: list[OpsOperationalAlertRead]


class OpsTranscriptRetentionRead(BaseModel):
    """Operator-facing state for one raw transcript asset."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    episode_title: str
    podcast_name: str
    provider: str
    fetched_at: datetime | None
    tier: str
    policy_version: str | None
    retention_exempt_sample: bool
    source_retention_opt_out: bool
    purge_eligible_at: datetime | None
    purged_at: datetime | None
    has_raw_payload: bool
    has_stored_artifact: bool
    digest_id: int | None


class OpsRetentionDecisionRead(BaseModel):
    """Lifecycle decision produced by a retention evaluation."""

    tier: str
    purge_eligible: bool
    purge_blockers: list[str]
    retention_suppressed: bool
    age_days: int


class OpsRetentionPreviewRead(BaseModel):
    """Dry-run lifecycle result plus derivative safety gate."""

    transcript: OpsTranscriptRetentionRead
    decision: OpsRetentionDecisionRead
    extraction_confidence: float | None
    derivative_coverage_ready: bool
    missing_query_classes: list[str]


class OpsTranscriptPurgeRead(BaseModel):
    """Result of an operator-approved transcript purge."""

    transcript: OpsTranscriptRetentionRead
    digest_id: int


class OpsAuditLogEntryRead(BaseModel):
    """One immutable audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    resource_type: str
    resource_id: int | None
    resource_identifier: str | None
    actor_name: str | None
    summary: str
    metadata_json: dict[str, Any] | None
    created_at: datetime


class OpsAuditLogListRead(BaseModel):
    """Paginated audit log payload."""

    items: list[OpsAuditLogEntryRead]
    total: int
    page: int
    per_page: int


OpsPodcastSortField = Literal["created_at", "name", "episode_count", "mention_count"]
OpsSortOrder = Literal["asc", "desc"]

__all__ = [
    "OpsAuditLogEntryRead",
    "OpsAuditLogListRead",
    "OpsAlertDeliveryRead",
    "OpsIngestionRunRead",
    "OpsMetricsRead",
    "OpsOperationalAlertListRead",
    "OpsOperationalAlertRead",
    "OpsPipelineActivityRead",
    "OpsPodcastCreateRequest",
    "OpsPodcastListRead",
    "OpsPodcastRead",
    "OpsPodcastSortField",
    "OpsPodcastSourcesInput",
    "OpsPodcastSourcesRead",
    "OpsPodcastUpdateRequest",
    "OpsRetentionDecisionRead",
    "OpsRetentionPreviewRead",
    "OpsReviewThroughputRead",
    "OpsSortOrder",
    "OpsTranscriptPurgeRead",
    "OpsTranscriptRetentionRead",
    "PodcastSourceType",
]
