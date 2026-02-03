"""Ops-focused v2 API endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.api.v2.identifiers import (
    decode_episode_id,
    decode_media_id,
    decode_podcast_id,
    decode_review_item_id,
    encode_audit_log_id,
    encode_episode_id,
    encode_ingestion_run_id,
    encode_media_id,
    encode_mention_candidate_id,
    encode_mention_id,
    encode_podcast_id,
    encode_review_item_id,
    encode_transcription_job_id,
)
from podex.api.v2.schemas import (
    CatalogSummary,
    EpisodeProcessingSummary,
    OpsAuditLogEntry,
    OpsAuditLogListResponse,
    OpsDashboardResponse,
    OpsEpisodeRerunRequest,
    OpsEpisodeRerunResponse,
    OpsIngestionRunSummary,
    OpsMediaMergeRequest,
    OpsMediaMergeResponse,
    OpsMergedMediaSummary,
    OpsPipelineActivityResponse,
    OpsPodcastCreateRequest,
    OpsPodcastListResponse,
    OpsPodcastSourceInput,
    OpsPodcastSourceSummary,
    OpsPodcastSummary,
    OpsPodcastUpdateRequest,
    OpsReviewDecisionRequest,
    OpsReviewMergeRequest,
    OpsReviewQueueCandidate,
    OpsReviewQueueCandidateProvenance,
    OpsReviewQueueExtractionJob,
    OpsReviewQueueItem,
    OpsReviewQueueListResponse,
    OpsReviewReclassifyRequest,
    OpsSearchProjectionIndexSummary,
    OpsSearchProjectionRepairCounts,
    OpsSearchProjectionRepairSummary,
    OpsSearchProjectionResponse,
    OpsTranscriptionJobSummary,
    PipelineSummary,
    SearchProjectionSummary,
    SourceCoverageSummary,
)
from podex.config import get_settings
from podex.database import get_db
from podex.logging_config import get_logger
from podex.models import (
    AuditAction,
    JobType,
    MediaType,
    PodcastStatus,
    ReviewItemStatus,
    ReviewPriority,
)
from podex.models.search_projection_repair import (
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
)
from podex.services.audit_log import (
    AuditLogEntryData,
    AuditLogListData,
    list_audit_logs,
    record_audit_log,
)
from podex.services.ops_media_commands import (
    OpsMediaMergeResultData,
    OpsMergedMediaSummaryData,
    merge_ops_media,
)
from podex.services.ops_pipeline_commands import (
    create_ops_ingestion_run,
    rerun_episode_processing_jobs,
)
from podex.services.ops_podcast_commands import (
    CreateOpsPodcastInputData,
    OpsPodcastSourceInputData,
    UpdateOpsPodcastInputData,
    archive_ops_podcast,
    create_ops_podcast,
    update_ops_podcast,
)
from podex.services.ops_podcast_queries import (
    OpsPodcastListData,
    OpsPodcastSourceData,
    OpsPodcastSummaryData,
    PodcastSourceType,
    list_ops_podcasts,
)
from podex.services.pipeline_queries import (
    IngestionRunSummaryData,
    TranscriptionJobSummaryData,
    list_recent_ingestion_runs,
    list_recent_transcription_jobs,
)
from podex.services.review_queue import (
    ReviewDecisionInputData,
    ReviewMergeInputData,
    ReviewQueueCandidateProvenanceData,
    ReviewQueueExtractionJobData,
    ReviewQueueItemData,
    ReviewQueueListData,
    ReviewReclassifyInputData,
    approve_review_queue_item,
    list_review_queue_items,
    merge_review_queue_item,
    reclassify_review_queue_item,
    reject_review_queue_item,
)
from podex.services.search import SearchSyncService, get_search_client
from podex.services.search_projection_queries import (
    SearchProjectionIndexData,
    SearchProjectionStatusData,
    get_search_projection_status,
)
from podex.services.search_projection_repairs import (
    SearchProjectionRepairCountsData,
    SearchProjectionRepairSummaryData,
    enqueue_extract_rerun_projection_repairs,
    ensure_search_projection_repair,
    get_search_projection_repair_counts,
    list_recent_search_projection_repairs,
    mark_search_projection_repair_completed,
)
from podex.services.status_queries import (
    get_episode_processing_counts,
    get_ingestion_run_counts,
    get_podcast_status_counts,
    get_source_coverage_counts,
    get_transcription_job_counts,
)

router = APIRouter(prefix="/ops", tags=["v2-ops"])
logger = get_logger(__name__)


def _sync_review_item_projection(*, db: Session, item: ReviewQueueItemData) -> None:
    """Best-effort sync of review publish side effects into search projection.

    Args:
        db: Database session.
        item: Persisted review queue item after approval or merge.
    """
    if item.candidate.media_id is None:
        return

    try:
        sync_service = SearchSyncService(client=get_search_client(), db=db)
        sync_service.sync_single_media(item.candidate.media_id)
        sync_service.sync_single_episode(item.episode_id)
        mark_search_projection_repair_completed(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=item.candidate.media_id,
        )
        mark_search_projection_repair_completed(
            db=db,
            resource_type=SearchProjectionRepairResourceType.EPISODE,
            resource_id=item.episode_id,
        )
    except Exception as error:
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=item.candidate.media_id,
            reason=SearchProjectionRepairReason.REVIEW_PUBLISH,
            status=SearchProjectionRepairStatus.FAILED,
            error_message=str(error),
        )
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.EPISODE,
            resource_id=item.episode_id,
            reason=SearchProjectionRepairReason.REVIEW_PUBLISH,
            status=SearchProjectionRepairStatus.FAILED,
            error_message=str(error),
        )
        logger.warning(
            "review_queue_projection_sync_failed",
            review_item_id=item.id,
            candidate_id=item.candidate.id,
            media_id=item.candidate.media_id,
            episode_id=item.episode_id,
            error=str(error),
        )


def _sync_media_merge_projection(
    *,
    db: Session,
    result: OpsMediaMergeResultData,
) -> None:
    """Best-effort sync of media merge side effects into search projection.

    Args:
        db: Database session.
        result: Persisted media merge result.
    """
    try:
        sync_service = SearchSyncService(client=get_search_client(), db=db)
        sync_service.sync_single_media(result.target.id)
        sync_service.delete_media(result.source_id)
        mark_search_projection_repair_completed(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=result.target.id,
        )
        mark_search_projection_repair_completed(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=result.source_id,
        )
    except Exception as error:
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=result.target.id,
            reason=SearchProjectionRepairReason.MEDIA_MERGE,
            status=SearchProjectionRepairStatus.FAILED,
            error_message=str(error),
        )
        ensure_search_projection_repair(
            db=db,
            resource_type=SearchProjectionRepairResourceType.MEDIA,
            resource_id=result.source_id,
            reason=SearchProjectionRepairReason.MEDIA_MERGE,
            status=SearchProjectionRepairStatus.FAILED,
            error_message=str(error),
        )
        logger.warning(
            "media_merge_projection_sync_failed",
            source_media_id=result.source_id,
            target_media_id=result.target.id,
            error=str(error),
        )


def _to_ops_podcast_source_summary(
    *,
    sources: OpsPodcastSourceData,
) -> OpsPodcastSourceSummary:
    """Convert shared source identifiers to the v2 ops schema.

    Args:
        sources: Shared podcast source identifiers.

    Returns:
        Ops podcast source summary.
    """
    return OpsPodcastSourceSummary(
        rss_url=sources.rss_url,
        spotify_id=sources.spotify_id,
        apple_id=sources.apple_id,
        youtube_channel_id=sources.youtube_channel_id,
        podscripts_slug=sources.podscripts_slug,
    )


def _to_ops_podcast_summary(
    *,
    podcast: OpsPodcastSummaryData,
) -> OpsPodcastSummary:
    """Convert shared podcast management data to the v2 ops schema.

    Args:
        podcast: Shared ops podcast summary.

    Returns:
        Ops podcast summary.
    """
    return OpsPodcastSummary(
        id=encode_podcast_id(podcast_id=podcast.id),
        name=podcast.name,
        slug=podcast.slug,
        status=podcast.status.value,
        description=podcast.description,
        cover_url=podcast.cover_url,
        created_at=podcast.created_at,
        discovery_source=podcast.discovery_source,
        episode_count=podcast.episode_count,
        mention_count=podcast.mention_count,
        sources=_to_ops_podcast_source_summary(sources=podcast.sources),
    )


def _to_ops_podcast_list_response(
    *,
    podcasts: OpsPodcastListData,
) -> OpsPodcastListResponse:
    """Convert shared podcast management data to the v2 ops list schema.

    Args:
        podcasts: Shared paginated podcast management payload.

    Returns:
        Paginated ops podcast list response.
    """
    return OpsPodcastListResponse(
        items=[_to_ops_podcast_summary(podcast=item) for item in podcasts.items],
        total=podcasts.total,
        page=podcasts.page,
        per_page=podcasts.per_page,
    )


def _to_ops_podcast_source_input(
    *,
    sources: OpsPodcastSourceInput,
) -> OpsPodcastSourceInputData:
    """Convert request source input to the shared mutation payload.

    Args:
        sources: Request source input.

    Returns:
        Shared source mutation payload.
    """
    return OpsPodcastSourceInputData(
        rss_url=sources.rss_url,
        spotify_id=sources.spotify_id,
        apple_id=sources.apple_id,
        youtube_channel_id=sources.youtube_channel_id,
        podscripts_slug=sources.podscripts_slug,
    )


def _validate_ops_podcast_update_request(
    *,
    payload: OpsPodcastUpdateRequest,
) -> None:
    """Validate partial podcast updates before applying mutations.

    Args:
        payload: Partial podcast update payload.

    Raises:
        HTTPException: If a non-clearable field is explicitly set to ``null``.
    """
    invalid_null_fields = [
        field_name
        for field_name in ("name", "slug", "status")
        if field_name in payload.model_fields_set
        and getattr(payload, field_name) is None
    ]
    if invalid_null_fields:
        field_list = ", ".join(invalid_null_fields)
        raise HTTPException(
            status_code=422,
            detail=f"Fields cannot be null: {field_list}",
        )


def _build_pipeline_summary(*, db: Session) -> PipelineSummary:
    """Build shared pipeline summary metrics for ops responses.

    Args:
        db: Database session.

    Returns:
        Pipeline summary metrics.
    """
    run_counts = get_ingestion_run_counts(db=db)
    job_counts = get_transcription_job_counts(db=db)
    repair_counts = get_search_projection_repair_counts(db=db)
    return PipelineSummary(
        ingestion_runs_total=run_counts["total"],
        ingestion_runs_in_progress=run_counts["in_progress"],
        ingestion_runs_failed=run_counts["failed"],
        ingestion_runs_completed=run_counts["completed"],
        transcription_jobs_pending=job_counts["pending"],
        transcription_jobs_failed=job_counts["failed"],
        transcription_jobs_in_progress=job_counts["in_progress"],
        projection_repairs_pending=repair_counts.pending,
        projection_repairs_failed=repair_counts.failed,
    )


def _to_ops_ingestion_run_summary(
    *,
    run: IngestionRunSummaryData,
) -> OpsIngestionRunSummary:
    """Convert shared run data to the v2 ops schema.

    Args:
        run: Shared run summary data.

    Returns:
        Ops ingestion run summary.
    """
    return OpsIngestionRunSummary(
        id=encode_ingestion_run_id(ingestion_run_id=run.id),
        status=run.status,
        error_summary=run.error_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        duration_seconds=run.duration_seconds,
    )


def _to_ops_transcription_job_summary(
    *,
    job: TranscriptionJobSummaryData,
) -> OpsTranscriptionJobSummary:
    """Convert shared job data to the v2 ops schema.

    Args:
        job: Shared transcription job summary data.

    Returns:
        Ops transcription job summary.
    """
    return OpsTranscriptionJobSummary(
        id=encode_transcription_job_id(transcription_job_id=job.id),
        episode_id=encode_episode_id(episode_id=job.episode_id),
        podcast_id=encode_podcast_id(podcast_id=job.podcast_id),
        podcast_name=job.podcast_name,
        podcast_slug=job.podcast_slug,
        episode_title=job.episode_title,
        job_type=job.job_type,
        status=job.status,
        backend=job.backend,
        model=job.model,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        duration_seconds=job.duration_seconds,
    )


def _to_ops_search_projection_index(
    *,
    index: SearchProjectionIndexData,
) -> OpsSearchProjectionIndexSummary:
    """Convert shared search projection index data to the v2 ops schema.

    Args:
        index: Shared search projection index data.

    Returns:
        Ops search projection index summary.
    """
    return OpsSearchProjectionIndexSummary(
        name=index.name,
        document_count=index.document_count,
        is_indexing=index.is_indexing,
    )


def _to_ops_search_projection_response(
    *,
    status: SearchProjectionStatusData,
    repair_counts: SearchProjectionRepairCountsData,
    repairs: list[SearchProjectionRepairSummaryData],
) -> OpsSearchProjectionResponse:
    """Convert shared search projection status to the v2 ops schema.

    Args:
        status: Shared search projection status.
        repair_counts: Aggregate repair counts.
        repairs: Recent repair records.

    Returns:
        Ops search projection response.
    """
    return OpsSearchProjectionResponse(
        configured=status.configured,
        healthy=status.healthy,
        indexes=[
            _to_ops_search_projection_index(index=index) for index in status.indexes
        ],
        repair_summary=OpsSearchProjectionRepairCounts(
            pending=repair_counts.pending,
            failed=repair_counts.failed,
            completed=repair_counts.completed,
        ),
        repairs=[_to_ops_search_projection_repair(repair=repair) for repair in repairs],
    )


def _to_ops_search_projection_repair(
    *,
    repair: SearchProjectionRepairSummaryData,
) -> OpsSearchProjectionRepairSummary:
    """Convert a shared projection repair record to the v2 ops schema.

    Args:
        repair: Shared repair summary.

    Returns:
        Search projection repair response payload.
    """
    return OpsSearchProjectionRepairSummary(
        id=f"spr_{repair.id}",
        resource_type=repair.resource_type,
        resource_id=(
            encode_episode_id(episode_id=repair.resource_id)
            if repair.resource_type is SearchProjectionRepairResourceType.EPISODE
            else encode_media_id(media_id=repair.resource_id)
        ),
        status=repair.status,
        reason=repair.reason,
        source_job_id=(
            encode_transcription_job_id(transcription_job_id=repair.source_job_id)
            if repair.source_job_id is not None
            else None
        ),
        error_message=repair.error_message,
        created_at=repair.created_at,
        updated_at=repair.updated_at,
        completed_at=repair.completed_at,
    )


def _to_ops_episode_rerun_response(
    *,
    episode_id: int,
    jobs: list[TranscriptionJobSummaryData],
) -> OpsEpisodeRerunResponse:
    """Convert created rerun jobs to the v2 ops schema.

    Args:
        episode_id: Internal episode identifier.
        jobs: Created transcription job summaries.

    Returns:
        Episode rerun response payload.
    """
    return OpsEpisodeRerunResponse(
        episode_id=encode_episode_id(episode_id=episode_id),
        jobs=[_to_ops_transcription_job_summary(job=job) for job in jobs],
    )


def _to_ops_merged_media_summary(
    *,
    media: OpsMergedMediaSummaryData,
) -> OpsMergedMediaSummary:
    """Convert merged media data to the v2 ops schema.

    Args:
        media: Shared merged media summary.

    Returns:
        Merged media summary payload.
    """
    return OpsMergedMediaSummary(
        id=encode_media_id(media_id=media.id),
        type=media.type,
        title=media.title,
        author=media.author,
        cover_url=media.cover_url,
        year=media.year,
        description=media.description,
        mention_count=media.mention_count,
        episode_count=media.episode_count,
    )


def _to_ops_media_merge_response(
    *,
    result: OpsMediaMergeResultData,
) -> OpsMediaMergeResponse:
    """Convert a media merge result to the v2 ops schema.

    Args:
        result: Shared media merge result.

    Returns:
        Media merge response payload.
    """
    return OpsMediaMergeResponse(
        source_id=encode_media_id(media_id=result.source_id),
        target=_to_ops_merged_media_summary(media=result.target),
    )


def _encode_ops_resource_id(
    *,
    resource_type: str,
    resource_id: int | None,
    resource_identifier: str | None,
) -> str | None:
    """Encode audit log resource identifiers for the API boundary.

    Args:
        resource_type: Affected resource type.
        resource_id: Optional internal numeric identifier.
        resource_identifier: Optional string identifier.

    Returns:
        Boundary-safe resource identifier when available.
    """
    if resource_id is None:
        return resource_identifier

    if resource_type == "episode":
        return encode_episode_id(episode_id=resource_id)
    if resource_type == "ingestion_run":
        return encode_ingestion_run_id(ingestion_run_id=resource_id)
    if resource_type == "media":
        return encode_media_id(media_id=resource_id)
    if resource_type == "mention_candidate":
        return encode_mention_candidate_id(mention_candidate_id=resource_id)
    if resource_type == "podcast":
        return encode_podcast_id(podcast_id=resource_id)
    if resource_type == "review_item":
        return encode_review_item_id(review_item_id=resource_id)
    if resource_type == "transcription_job":
        return encode_transcription_job_id(transcription_job_id=resource_id)

    return resource_identifier or str(resource_id)


def _to_ops_review_queue_candidate(
    *,
    candidate: ReviewQueueItemData,
) -> OpsReviewQueueCandidate:
    """Convert review queue candidate data to the v2 ops schema.

    Args:
        candidate: Shared review queue item data.

    Returns:
        Nested review candidate response payload.
    """
    return OpsReviewQueueCandidate(
        id=encode_mention_candidate_id(mention_candidate_id=candidate.candidate.id),
        type=MediaType(candidate.candidate.media_type),
        raw_title=candidate.candidate.raw_title,
        normalized_title=candidate.candidate.normalized_title,
        suggested_author=candidate.candidate.suggested_author,
        timestamp_seconds=candidate.candidate.timestamp_seconds,
        context=candidate.candidate.context,
        confidence=candidate.candidate.confidence,
        extraction_source=candidate.candidate.extraction_source,
        source_job_id=(
            encode_transcription_job_id(
                transcription_job_id=candidate.candidate.source_job_id,
            )
            if candidate.candidate.source_job_id is not None
            else None
        ),
        source_job_status=candidate.candidate.source_job_status,
        source_job_backend=candidate.candidate.source_job_backend,
        source_job_model=candidate.candidate.source_job_model,
        source_job_created_at=candidate.candidate.source_job_created_at,
        state=candidate.candidate.state,
        media_id=(
            encode_media_id(media_id=candidate.candidate.media_id)
            if candidate.candidate.media_id is not None
            else None
        ),
        mention_id=(
            encode_mention_id(mention_id=candidate.candidate.mention_id)
            if candidate.candidate.mention_id is not None
            else None
        ),
        created_at=candidate.candidate.created_at,
        reviewed_at=candidate.candidate.reviewed_at,
        extraction_jobs=[
            _to_ops_review_queue_extraction_job(job=job)
            for job in candidate.candidate.extraction_jobs
        ],
        provenance=[
            _to_ops_review_queue_candidate_provenance(provenance=provenance)
            for provenance in candidate.candidate.provenance
        ],
    )


def _to_ops_review_queue_extraction_job(
    *,
    job: ReviewQueueExtractionJobData,
) -> OpsReviewQueueExtractionJob:
    """Convert shared extraction job data to the v2 ops schema.

    Args:
        job: Shared extraction job payload.

    Returns:
        Nested review candidate extraction job payload.
    """
    return OpsReviewQueueExtractionJob(
        id=encode_transcription_job_id(transcription_job_id=job.id),
        status=job.status,
        backend=job.backend,
        model=job.model,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        is_source_job=job.is_source_job,
    )


def _to_ops_review_queue_candidate_provenance(
    *,
    provenance: ReviewQueueCandidateProvenanceData,
) -> OpsReviewQueueCandidateProvenance:
    """Convert shared candidate provenance data to the v2 ops schema.

    Args:
        provenance: Shared review candidate provenance data.

    Returns:
        Nested review candidate provenance payload.
    """
    return OpsReviewQueueCandidateProvenance(
        id=f"prov_{provenance.id}",
        event_type=provenance.event_type,
        change_summary=provenance.change_summary,
        raw_title=provenance.raw_title,
        normalized_title=provenance.normalized_title,
        suggested_author=provenance.suggested_author,
        timestamp_seconds=provenance.timestamp_seconds,
        context=provenance.context,
        confidence=provenance.confidence,
        extraction_source=provenance.extraction_source,
        source_job_id=(
            encode_transcription_job_id(
                transcription_job_id=provenance.source_job_id,
            )
            if provenance.source_job_id is not None
            else None
        ),
        source_job_status=provenance.source_job_status,
        source_job_backend=provenance.source_job_backend,
        source_job_model=provenance.source_job_model,
        source_job_created_at=provenance.source_job_created_at,
        media_id=(
            encode_media_id(media_id=provenance.media_id)
            if provenance.media_id is not None
            else None
        ),
        changed_fields=provenance.changed_fields,
        created_at=provenance.created_at,
    )


def _to_ops_review_queue_item(
    *,
    item: ReviewQueueItemData,
) -> OpsReviewQueueItem:
    """Convert shared review queue data to the v2 ops schema.

    Args:
        item: Shared review queue item.

    Returns:
        Review queue item response payload.
    """
    return OpsReviewQueueItem(
        id=encode_review_item_id(review_item_id=item.id),
        status=item.status,
        priority=item.priority,
        assigned_to=item.assigned_to,
        decision_note=item.decision_note,
        created_at=item.created_at,
        updated_at=item.updated_at,
        decided_at=item.decided_at,
        episode_id=encode_episode_id(episode_id=item.episode_id),
        episode_title=item.episode_title,
        podcast_id=encode_podcast_id(podcast_id=item.podcast_id),
        podcast_name=item.podcast_name,
        podcast_slug=item.podcast_slug,
        target_media_id=(
            encode_media_id(media_id=item.target_media_id)
            if item.target_media_id is not None
            else None
        ),
        candidate=_to_ops_review_queue_candidate(candidate=item),
    )


def _to_ops_review_queue_list_response(
    *,
    queue: ReviewQueueListData,
) -> OpsReviewQueueListResponse:
    """Convert shared review queue list data to the v2 ops schema.

    Args:
        queue: Shared review queue list payload.

    Returns:
        Paginated review queue response.
    """
    return OpsReviewQueueListResponse(
        items=[_to_ops_review_queue_item(item=item) for item in queue.items],
        total=queue.total,
        page=queue.page,
        per_page=queue.per_page,
    )


def _to_ops_audit_log_entry(
    *,
    entry: AuditLogEntryData,
) -> OpsAuditLogEntry:
    """Convert shared audit log data to the v2 ops schema.

    Args:
        entry: Shared audit log entry.

    Returns:
        Audit log response entry.
    """
    return OpsAuditLogEntry(
        id=encode_audit_log_id(audit_log_id=entry.id),
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=_encode_ops_resource_id(
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            resource_identifier=entry.resource_identifier,
        ),
        actor_name=entry.actor_name,
        summary=entry.summary,
        metadata_json=entry.metadata_json,
        created_at=entry.created_at,
    )


def _to_ops_audit_log_list_response(
    *,
    entries: AuditLogListData,
) -> OpsAuditLogListResponse:
    """Convert shared audit log list data to the v2 ops schema.

    Args:
        entries: Shared audit log list payload.

    Returns:
        Paginated audit log response.
    """
    return OpsAuditLogListResponse(
        items=[_to_ops_audit_log_entry(entry=entry) for entry in entries.items],
        total=entries.total,
        page=entries.page,
        per_page=entries.per_page,
    )


@router.get("/dashboard", response_model=OpsDashboardResponse)
def get_ops_dashboard(
    db: Session = Depends(get_db),
) -> OpsDashboardResponse:
    """Get aggregated ops dashboard metrics.

    Args:
        db: Database session.

    Returns:
        Aggregated operational metrics for the v2 dashboard.
    """
    settings = get_settings()
    podcast_counts = get_podcast_status_counts(db=db)
    source_counts = get_source_coverage_counts(db=db)
    episode_counts = get_episode_processing_counts(db=db)

    return OpsDashboardResponse(
        catalog=CatalogSummary(
            total_podcasts=podcast_counts["total"],
            active_podcasts=podcast_counts["active"],
            watchlist_podcasts=podcast_counts["watchlist"],
            paused_podcasts=podcast_counts["paused"],
        ),
        sources=SourceCoverageSummary(
            with_rss=source_counts["with_rss"],
            with_spotify=source_counts["with_spotify"],
            with_podscripts=source_counts["with_podscripts"],
            with_youtube=source_counts["with_youtube"],
        ),
        episodes=EpisodeProcessingSummary(
            total_known=episode_counts["total_known"],
            transcribed=episode_counts["transcribed"],
            extracted=episode_counts["extracted"],
        ),
        pipelines=_build_pipeline_summary(db=db),
        search=SearchProjectionSummary(enabled=settings.meilisearch_enabled),
    )


@router.get("/podcasts", response_model=OpsPodcastListResponse)
def get_ops_podcasts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: PodcastStatus | None = Query(None),
    source: PodcastSourceType | None = Query(None),
    sort: Literal["created_at", "name", "episode_count", "mention_count"] = (
        "created_at"
    ),
    order: Literal["asc", "desc"] = "desc",
    db: Session = Depends(get_db),
) -> OpsPodcastListResponse:
    """List podcasts for ops catalog management views.

    Args:
        page: Requested page number.
        per_page: Number of items per page.
        status: Optional podcast status filter.
        source: Optional source-presence filter.
        sort: Sort field.
        order: Sort direction.
        db: Database session.

    Returns:
        Paginated ops podcast management response.
    """
    return _to_ops_podcast_list_response(
        podcasts=list_ops_podcasts(
            db=db,
            page=page,
            per_page=per_page,
            status=status,
            source=source,
            sort=sort,
            order=order,
        )
    )


@router.post(
    "/podcasts",
    response_model=OpsPodcastSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_ops_podcast_route(
    payload: OpsPodcastCreateRequest,
    db: Session = Depends(get_db),
) -> OpsPodcastSummary:
    """Create a podcast for ops catalog management.

    Args:
        payload: Podcast creation payload.
        db: Database session.

    Returns:
        Created podcast summary.

    Raises:
        HTTPException: If the slug conflicts with an existing podcast.
    """
    try:
        podcast = create_ops_podcast(
            db=db,
            payload=CreateOpsPodcastInputData(
                name=payload.name,
                slug=payload.slug,
                status=payload.status,
                description=payload.description,
                cover_url=payload.cover_url,
                discovery_source=payload.discovery_source,
                sources=_to_ops_podcast_source_input(sources=payload.sources),
            ),
        )
        record_audit_log(
            db=db,
            action=AuditAction.CREATE_PODCAST,
            resource_type="podcast",
            resource_id=podcast.id,
            summary=f"Created podcast {podcast.name}",
            metadata_json={
                "slug": podcast.slug,
                "status": podcast.status.value,
            },
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Podcast slug already exists",
        ) from error

    return _to_ops_podcast_summary(podcast=podcast)


@router.patch("/podcasts/{podcast_id}", response_model=OpsPodcastSummary)
def update_ops_podcast_route(
    podcast_id: str,
    payload: OpsPodcastUpdateRequest,
    db: Session = Depends(get_db),
) -> OpsPodcastSummary:
    """Update a podcast for ops catalog management.

    Args:
        podcast_id: Opaque podcast identifier.
        payload: Partial podcast update payload.
        db: Database session.

    Returns:
        Updated podcast summary.

    Raises:
        HTTPException: If the podcast does not exist or the slug conflicts.
    """
    try:
        internal_podcast_id = decode_podcast_id(podcast_id=podcast_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Podcast not found") from error

    _validate_ops_podcast_update_request(payload=payload)

    try:
        podcast = update_ops_podcast(
            db=db,
            podcast_id=internal_podcast_id,
            payload=UpdateOpsPodcastInputData(
                provided_fields=frozenset(payload.model_fields_set),
                source_fields=(
                    frozenset(payload.sources.model_fields_set)
                    if payload.sources is not None
                    else frozenset()
                ),
                name=payload.name,
                slug=payload.slug,
                status=payload.status,
                description=payload.description,
                cover_url=payload.cover_url,
                discovery_source=payload.discovery_source,
                sources=(
                    _to_ops_podcast_source_input(sources=payload.sources)
                    if payload.sources is not None
                    else None
                ),
            ),
        )
        if podcast is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="Podcast not found")
        record_audit_log(
            db=db,
            action=AuditAction.UPDATE_PODCAST,
            resource_type="podcast",
            resource_id=podcast.id,
            summary=f"Updated podcast {podcast.name}",
            metadata_json={
                "updated_fields": sorted(payload.model_fields_set),
                "source_fields": (
                    sorted(payload.sources.model_fields_set)
                    if payload.sources is not None
                    else []
                ),
            },
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Podcast slug already exists",
        ) from error

    return _to_ops_podcast_summary(podcast=podcast)


@router.post("/podcasts/{podcast_id}/archive", response_model=OpsPodcastSummary)
def archive_ops_podcast_route(
    podcast_id: str,
    db: Session = Depends(get_db),
) -> OpsPodcastSummary:
    """Archive a podcast for ops catalog management.

    Args:
        podcast_id: Opaque podcast identifier.
        db: Database session.

    Returns:
        Archived podcast summary.

    Raises:
        HTTPException: If the podcast does not exist.
    """
    try:
        internal_podcast_id = decode_podcast_id(podcast_id=podcast_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Podcast not found") from error

    podcast = archive_ops_podcast(db=db, podcast_id=internal_podcast_id)
    if podcast is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Podcast not found")

    record_audit_log(
        db=db,
        action=AuditAction.ARCHIVE_PODCAST,
        resource_type="podcast",
        resource_id=podcast.id,
        summary=f"Archived podcast {podcast.name}",
        metadata_json={"status": podcast.status.value},
    )
    db.commit()
    return _to_ops_podcast_summary(podcast=podcast)


@router.get("/pipelines", response_model=OpsPipelineActivityResponse)
def get_ops_pipeline_activity(
    run_limit: int = Query(10, ge=1, le=50),
    job_limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> OpsPipelineActivityResponse:
    """Get recent ingestion run and job activity for ops views.

    Args:
        run_limit: Maximum number of ingestion runs to return.
        job_limit: Maximum number of transcription jobs to return.
        db: Database session.

    Returns:
        Recent pipeline activity and queue summary.
    """
    return OpsPipelineActivityResponse(
        summary=_build_pipeline_summary(db=db),
        runs=[
            _to_ops_ingestion_run_summary(run=run)
            for run in list_recent_ingestion_runs(db=db, limit=run_limit)
        ],
        jobs=[
            _to_ops_transcription_job_summary(job=job)
            for job in list_recent_transcription_jobs(db=db, limit=job_limit)
        ],
    )


@router.post(
    "/pipelines/run",
    response_model=OpsIngestionRunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def run_ops_pipeline(
    db: Session = Depends(get_db),
) -> OpsIngestionRunSummary:
    """Trigger a new ingestion run from the v2 ops API.

    Args:
        db: Database session.

    Returns:
        Created ingestion run summary.
    """
    run = create_ops_ingestion_run(db=db)
    record_audit_log(
        db=db,
        action=AuditAction.RUN_PIPELINE,
        resource_type="ingestion_run",
        resource_id=run.id,
        summary="Triggered a pipeline run",
        metadata_json={"status": run.status},
    )
    db.commit()
    return _to_ops_ingestion_run_summary(run=run)


@router.get("/review-queue", response_model=OpsReviewQueueListResponse)
def get_ops_review_queue(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: ReviewItemStatus | None = Query(None),
    priority: ReviewPriority | None = Query(None),
    db: Session = Depends(get_db),
) -> OpsReviewQueueListResponse:
    """List review queue items for ops views.

    Args:
        page: Requested page number.
        per_page: Number of items per page.
        status: Optional review status filter.
        priority: Optional priority filter.
        db: Database session.

    Returns:
        Paginated review queue response.
    """
    return _to_ops_review_queue_list_response(
        queue=list_review_queue_items(
            db=db,
            page=page,
            per_page=per_page,
            status=status,
            priority=priority,
        ),
    )


@router.post(
    "/review-queue/{review_item_id}/approve",
    response_model=OpsReviewQueueItem,
)
def approve_ops_review_item(
    review_item_id: str,
    payload: OpsReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> OpsReviewQueueItem:
    """Approve a review queue item and publish its candidate.

    Args:
        review_item_id: Opaque review item identifier.
        payload: Decision payload.
        db: Database session.

    Returns:
        Updated review queue item.

    Raises:
        HTTPException: If the item does not exist or cannot be decided.
    """
    try:
        internal_review_item_id = decode_review_item_id(review_item_id=review_item_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review item not found") from error

    try:
        item = approve_review_queue_item(
            db=db,
            review_item_id=internal_review_item_id,
            payload=ReviewDecisionInputData(
                actor_name=payload.actor_name,
                note=payload.note,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    if item is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Review item not found")

    record_audit_log(
        db=db,
        action=AuditAction.APPROVE_REVIEW_ITEM,
        resource_type="review_item",
        resource_id=item.id,
        actor_name=payload.actor_name,
        summary=f"Approved review item for {item.candidate.raw_title}",
        metadata_json={
            "candidate_id": item.candidate.id,
            "media_id": item.candidate.media_id,
            "mention_id": item.candidate.mention_id,
        },
    )
    db.commit()
    _sync_review_item_projection(db=db, item=item)
    return _to_ops_review_queue_item(item=item)


@router.post("/review-queue/{review_item_id}/reject", response_model=OpsReviewQueueItem)
def reject_ops_review_item(
    review_item_id: str,
    payload: OpsReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> OpsReviewQueueItem:
    """Reject a review queue item.

    Args:
        review_item_id: Opaque review item identifier.
        payload: Decision payload.
        db: Database session.

    Returns:
        Updated review queue item.

    Raises:
        HTTPException: If the item does not exist or cannot be decided.
    """
    try:
        internal_review_item_id = decode_review_item_id(review_item_id=review_item_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review item not found") from error

    try:
        item = reject_review_queue_item(
            db=db,
            review_item_id=internal_review_item_id,
            payload=ReviewDecisionInputData(
                actor_name=payload.actor_name,
                note=payload.note,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    if item is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Review item not found")

    record_audit_log(
        db=db,
        action=AuditAction.REJECT_REVIEW_ITEM,
        resource_type="review_item",
        resource_id=item.id,
        actor_name=payload.actor_name,
        summary=f"Rejected review item for {item.candidate.raw_title}",
        metadata_json={"candidate_id": item.candidate.id},
    )
    db.commit()
    return _to_ops_review_queue_item(item=item)


@router.post("/review-queue/{review_item_id}/merge", response_model=OpsReviewQueueItem)
def merge_ops_review_item(
    review_item_id: str,
    payload: OpsReviewMergeRequest,
    db: Session = Depends(get_db),
) -> OpsReviewQueueItem:
    """Merge a review candidate into an existing canonical media record.

    Args:
        review_item_id: Opaque review item identifier.
        payload: Merge decision payload.
        db: Database session.

    Returns:
        Updated review queue item.

    Raises:
        HTTPException: If the item or target media does not exist,
            or the item cannot be decided.
    """
    try:
        internal_review_item_id = decode_review_item_id(review_item_id=review_item_id)
        target_media_id = decode_media_id(media_id=payload.target_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review item not found") from error

    try:
        item = merge_review_queue_item(
            db=db,
            review_item_id=internal_review_item_id,
            payload=ReviewMergeInputData(
                actor_name=payload.actor_name,
                note=payload.note,
                target_media_id=target_media_id,
            ),
        )
    except ValueError as error:
        db.rollback()
        detail = str(error)
        status_code = 404 if detail == "Target media not found" else 409
        raise HTTPException(status_code=status_code, detail=detail) from error

    if item is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Review item not found")

    record_audit_log(
        db=db,
        action=AuditAction.MERGE_REVIEW_ITEM,
        resource_type="review_item",
        resource_id=item.id,
        actor_name=payload.actor_name,
        summary=f"Merged review item for {item.candidate.raw_title}",
        metadata_json={
            "candidate_id": item.candidate.id,
            "target_media_id": item.target_media_id,
            "mention_id": item.candidate.mention_id,
        },
    )
    db.commit()
    _sync_review_item_projection(db=db, item=item)
    return _to_ops_review_queue_item(item=item)


@router.post(
    "/review-queue/{review_item_id}/reclassify",
    response_model=OpsReviewQueueItem,
)
def reclassify_ops_review_item(
    review_item_id: str,
    payload: OpsReviewReclassifyRequest,
    db: Session = Depends(get_db),
) -> OpsReviewQueueItem:
    """Reclassify a pending review candidate.

    Args:
        review_item_id: Opaque review item identifier.
        payload: Reclassification payload.
        db: Database session.

    Returns:
        Updated review queue item.

    Raises:
        HTTPException: If the item does not exist or cannot be reclassified.
    """
    try:
        internal_review_item_id = decode_review_item_id(review_item_id=review_item_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review item not found") from error

    try:
        item = reclassify_review_queue_item(
            db=db,
            review_item_id=internal_review_item_id,
            payload=ReviewReclassifyInputData(
                media_type=payload.type.value if payload.type is not None else None,
                raw_title=payload.raw_title,
                normalized_title=payload.normalized_title,
                suggested_author=payload.suggested_author,
                clear_suggested_author=(
                    "suggested_author" in payload.model_fields_set
                    and payload.suggested_author is None
                ),
                actor_name=payload.actor_name,
                note=payload.note,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    if item is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Review item not found")

    changed_fields = [
        field_name
        for field_name in payload.model_fields_set
        if field_name not in {"actor_name", "note"}
    ]
    record_audit_log(
        db=db,
        action=AuditAction.RECLASSIFY_REVIEW_ITEM,
        resource_type="review_item",
        resource_id=item.id,
        actor_name=payload.actor_name,
        summary=f"Reclassified review item for {item.candidate.raw_title}",
        metadata_json={
            "candidate_id": item.candidate.id,
            "changed_fields": changed_fields,
            "new_media_type": item.candidate.media_type,
        },
    )
    db.commit()
    return _to_ops_review_queue_item(item=item)


@router.post(
    "/episodes/{episode_id}/rerun",
    response_model=OpsEpisodeRerunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rerun_ops_episode(
    episode_id: str,
    payload: OpsEpisodeRerunRequest,
    db: Session = Depends(get_db),
) -> OpsEpisodeRerunResponse:
    """Queue processing jobs to rerun an episode.

    Args:
        episode_id: Opaque episode identifier.
        payload: Requested episode rerun job types.
        db: Database session.

    Returns:
        Created rerun jobs for the episode.

    Raises:
        HTTPException: If the episode does not exist.
    """
    try:
        internal_episode_id = decode_episode_id(episode_id=episode_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Episode not found") from error

    deduped_job_types = tuple(dict.fromkeys(payload.job_types))
    jobs = rerun_episode_processing_jobs(
        db=db,
        episode_id=internal_episode_id,
        job_types=deduped_job_types,
    )
    if jobs is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Episode not found")

    record_audit_log(
        db=db,
        action=AuditAction.RERUN_EPISODE,
        resource_type="episode",
        resource_id=internal_episode_id,
        summary="Queued episode rerun jobs",
        metadata_json={
            "job_types": [job_type.value for job_type in deduped_job_types],
            "job_ids": [job.id for job in jobs],
        },
    )
    if JobType.EXTRACT in deduped_job_types:
        extract_job_id = next(
            (job.id for job in jobs if job.job_type == JobType.EXTRACT.value),
            None,
        )
        if extract_job_id is not None:
            repairs = enqueue_extract_rerun_projection_repairs(
                db=db,
                episode_id=internal_episode_id,
                source_job_id=extract_job_id,
            )
            if repairs:
                record_audit_log(
                    db=db,
                    action=AuditAction.RERUN_EPISODE,
                    resource_type="search_projection",
                    resource_identifier="repair_queue",
                    summary="Queued search projection repairs for extract rerun",
                    metadata_json={
                        "episode_id": internal_episode_id,
                        "source_job_id": extract_job_id,
                        "repair_ids": [repair.id for repair in repairs],
                    },
                )
    db.commit()
    return _to_ops_episode_rerun_response(
        episode_id=internal_episode_id,
        jobs=jobs,
    )


@router.post("/media/{media_id}/merge", response_model=OpsMediaMergeResponse)
def merge_ops_media_route(
    media_id: str,
    payload: OpsMediaMergeRequest,
    db: Session = Depends(get_db),
) -> OpsMediaMergeResponse:
    """Merge one media record into another for ops catalog maintenance.

    Args:
        media_id: Opaque media identifier to merge from.
        payload: Merge target payload.
        db: Database session.

    Returns:
        Merge result pointing to the surviving target media.

    Raises:
        HTTPException: If either media record is missing or the merge is invalid.
    """
    try:
        source_media_id = decode_media_id(media_id=media_id)
        target_media_id = decode_media_id(media_id=payload.target_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    try:
        merge_result = merge_ops_media(
            db=db,
            source_media_id=source_media_id,
            target_media_id=target_media_id,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error

    if merge_result is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Media not found")

    record_audit_log(
        db=db,
        action=AuditAction.MERGE_MEDIA,
        resource_type="media",
        resource_id=merge_result.target.id,
        summary=f"Merged media {source_media_id} into {target_media_id}",
        metadata_json={
            "source_media_id": source_media_id,
            "target_media_id": target_media_id,
        },
    )
    db.commit()
    _sync_media_merge_projection(db=db, result=merge_result)
    return _to_ops_media_merge_response(result=merge_result)


@router.get("/audit-log", response_model=OpsAuditLogListResponse)
def get_ops_audit_log(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    action: AuditAction | None = Query(None),
    resource_type: str | None = Query(None),
    db: Session = Depends(get_db),
) -> OpsAuditLogListResponse:
    """List audit log entries for ops views.

    Args:
        page: Requested page number.
        per_page: Number of items per page.
        action: Optional action filter.
        resource_type: Optional resource type filter.
        db: Database session.

    Returns:
        Paginated audit log response.
    """
    return _to_ops_audit_log_list_response(
        entries=list_audit_logs(
            db=db,
            page=page,
            per_page=per_page,
            action=action,
            resource_type=resource_type,
        ),
    )


@router.get("/search", response_model=OpsSearchProjectionResponse)
def get_ops_search_projection(
    repair_limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> OpsSearchProjectionResponse:
    """Get search projection health and per-index stats for ops views.

    Args:
        repair_limit: Maximum number of repair records to return.
        db: Database session.

    Returns:
        Search projection health and index statistics.
    """
    settings = get_settings()
    status = get_search_projection_status(
        client=get_search_client(),
        configured_enabled=settings.meilisearch_enabled,
    )
    return _to_ops_search_projection_response(
        status=status,
        repair_counts=get_search_projection_repair_counts(db=db),
        repairs=list_recent_search_projection_repairs(db=db, limit=repair_limit),
    )
