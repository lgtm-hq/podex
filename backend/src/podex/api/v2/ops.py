"""Ops-focused v2 API endpoints."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.api.v2.identifiers import (
    decode_episode_id,
    decode_media_id,
    decode_mention_id,
    decode_podcast_id,
    decode_review_item_id,
    decode_takedown_request_id,
    decode_transcript_id,
    encode_audit_log_id,
    encode_episode_id,
    encode_ingestion_run_id,
    encode_media_id,
    encode_mention_candidate_id,
    encode_mention_id,
    encode_pipeline_schedule_id,
    encode_podcast_id,
    encode_review_item_id,
    encode_scheduled_work_id,
    encode_takedown_request_id,
    encode_transcript_artifact_id,
    encode_transcript_digest_id,
    encode_transcript_id,
    encode_transcription_job_id,
)
from podex.api.v2.schemas import (
    CatalogSummary,
    EpisodeProcessingSummary,
    OpsAlertDeliveryMetrics,
    OpsAuditLogEntry,
    OpsAuditLogListResponse,
    OpsDashboardResponse,
    OpsEpisodeRerunRequest,
    OpsEpisodeRerunResponse,
    OpsIngestionRunSummary,
    OpsMediaAlias,
    OpsMediaAliasRequest,
    OpsMediaDetailResponse,
    OpsMediaExternalRef,
    OpsMediaExternalRefRequest,
    OpsMediaMention,
    OpsMediaMergeAliasAddition,
    OpsMediaMergeFieldChange,
    OpsMediaMergePreviewResponse,
    OpsMediaMergeRequest,
    OpsMediaMergeResponse,
    OpsMediaRelation,
    OpsMediaSplitRequest,
    OpsMediaSplitResponse,
    OpsMediaUpdateRequest,
    OpsMergedMediaSummary,
    OpsOperationalAlert,
    OpsOperationalAlertResponse,
    OpsOperationalMetricsResponse,
    OpsPipelineActivityResponse,
    OpsPipelineScheduleSummary,
    OpsPodcastCreateRequest,
    OpsPodcastListResponse,
    OpsPodcastSourceInput,
    OpsPodcastSourceSummary,
    OpsPodcastSummary,
    OpsPodcastUpdateRequest,
    OpsProjectionLagMetrics,
    OpsRetentionSamplingRecalculateRequest,
    OpsRetentionSamplingReportResponse,
    OpsRetentionSamplingStratum,
    OpsReviewDecisionRequest,
    OpsReviewMergeRequest,
    OpsReviewQueueCandidate,
    OpsReviewQueueCandidateProvenance,
    OpsReviewQueueExtractionJob,
    OpsReviewQueueItem,
    OpsReviewQueueListResponse,
    OpsReviewReclassifyRequest,
    OpsReviewSplitRequest,
    OpsReviewSplitResponse,
    OpsReviewThroughputMetrics,
    OpsScheduledWorkItemSummary,
    OpsScheduledWorkResponse,
    OpsSearchAnalyticsResponse,
    OpsSearchProjectionIndexSummary,
    OpsSearchProjectionRepairCounts,
    OpsSearchProjectionRepairSummary,
    OpsSearchProjectionResponse,
    OpsSearchQueryMetric,
    OpsSearchReindexRequest,
    OpsSearchReindexResponse,
    OpsSearchTuningApplyRequest,
    OpsSearchTuningApplyResponse,
    OpsSearchTuningPreviewRequest,
    OpsSearchTuningPreviewResponse,
    OpsTakedownDecisionRequest,
    OpsTakedownRequestListResponse,
    OpsTakedownRequestSummary,
    OpsTranscriptDigestResponse,
    OpsTranscriptionJobSummary,
    OpsTranscriptPurgeResponse,
    OpsTranscriptReacquireRequest,
    OpsTranscriptReacquireResponse,
    OpsTranscriptRetentionListResponse,
    OpsTranscriptRetentionPolicyRequest,
    OpsTranscriptRetentionPreviewResponse,
    OpsTranscriptRetentionSummary,
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
    ScheduledWorkStatus,
    TakedownRequest,
    TakedownRequestStatus,
    TakedownSubjectType,
    Transcript,
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
from podex.services.operational_alerts import (
    OperationalAlertThresholdsData,
    evaluate_operational_alerts,
)
from podex.services.ops_media_commands import (
    OpsMediaDetailData,
    OpsMediaMergePreviewData,
    OpsMediaMergeResultData,
    OpsMediaSplitResultData,
    OpsMergedMediaSummaryData,
    SplitOpsMediaInputData,
    UpdateOpsMediaInputData,
    UpsertOpsMediaExternalRefInputData,
    add_ops_media_alias,
    get_ops_media_detail,
    merge_ops_media,
    preview_ops_media_merge,
    split_ops_media,
    update_ops_media,
    upsert_ops_media_external_ref,
)
from podex.services.ops_metrics import get_operational_metrics
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
from podex.services.ops_retention_commands import (
    OpsTranscriptReacquisitionResultData,
    OpsTranscriptRetentionData,
    OpsTranscriptRetentionPreviewData,
    apply_ops_transcript_retention,
    list_ops_transcript_retention,
    preview_ops_transcript_retention,
    purge_ops_transcript,
    reacquire_ops_transcript,
)
from podex.services.ops_search_commands import (
    OpsSearchReindexInputData,
    queue_ops_search_reindex,
)
from podex.services.pipeline_queries import (
    IngestionRunSummaryData,
    TranscriptionJobSummaryData,
    list_recent_ingestion_runs,
    list_recent_transcription_jobs,
)
from podex.services.retention_sampling import (
    RetentionSamplingReportData,
    get_retention_sampling_report,
    recalculate_retention_sample,
)
from podex.services.review_queue import (
    ReviewDecisionInputData,
    ReviewMergeInputData,
    ReviewQueueCandidateProvenanceData,
    ReviewQueueExtractionJobData,
    ReviewQueueItemData,
    ReviewQueueListData,
    ReviewReclassifyInputData,
    ReviewSplitCandidateInputData,
    ReviewSplitInputData,
    ReviewSplitResultData,
    approve_review_queue_item,
    list_review_queue_items,
    merge_review_queue_item,
    reclassify_review_queue_item,
    reject_review_queue_item,
    split_review_queue_item,
)
from podex.services.scheduled_work import (
    PipelineScheduleSummaryData,
    ScheduledWorkItemSummaryData,
    list_pipeline_schedules,
    list_scheduled_work_items,
    plan_due_scheduled_work,
)
from podex.services.search import SearchSyncService, get_search_client
from podex.services.search_analytics import get_search_analytics_summary
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
from podex.services.takedown_requests import (
    TakedownDecisionInputData,
    TakedownExecutionResultData,
    decide_takedown_request,
    execute_approved_takedown_request,
    list_takedown_requests,
)
from podex.services.transcript_artifacts import (
    TranscriptArtifactStore,
    build_transcript_artifact_store,
)
from podex.services.transcript_retention import TranscriptRetentionPolicy
from podex.services.transcript_retention_policies import (
    upsert_transcript_source_retention_policy,
)
from podex.services.transcript_source import TranscriptAcquirer

router = APIRouter(prefix="/ops", tags=["v2-ops"])


def get_ops_transcript_artifact_store() -> TranscriptArtifactStore | None:
    """Get the configured private transcript artifact storage adapter."""
    return build_transcript_artifact_store(settings=get_settings())


def get_ops_transcript_acquirer() -> TranscriptAcquirer:
    """Build the provider orchestrator used for explicit re-acquisition."""
    return TranscriptAcquirer()


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


def _sync_media_projection_update(
    *,
    db: Session,
    media_ids: tuple[int, ...],
    reason: SearchProjectionRepairReason,
) -> None:
    """Best-effort sync of edited media records into the search projection."""
    try:
        sync_service = SearchSyncService(client=get_search_client(), db=db)
        for media_id in media_ids:
            sync_service.sync_single_media(media_id)
            mark_search_projection_repair_completed(
                db=db,
                resource_type=SearchProjectionRepairResourceType.MEDIA,
                resource_id=media_id,
            )
    except Exception as error:
        for media_id in media_ids:
            ensure_search_projection_repair(
                db=db,
                resource_type=SearchProjectionRepairResourceType.MEDIA,
                resource_id=media_id,
                reason=reason,
                status=SearchProjectionRepairStatus.FAILED,
                error_message=str(error),
            )
        logger.warning(
            "media_update_projection_sync_failed",
            media_ids=media_ids,
            reason=reason.value,
            error=str(error),
        )


def _sync_takedown_projection(
    *,
    db: Session,
    request: TakedownRequest,
    result: TakedownExecutionResultData,
) -> None:
    """Reflect takedown suppression in public search projection."""
    actions = set(request.requested_actions_json)
    should_remove_episodes = (
        "purge_search_projection" in actions
        and request.subject_type != TakedownSubjectType.MENTION.value
    )
    try:
        sync_service = SearchSyncService(client=get_search_client(), db=db)
        for media_id in result.media_ids:
            sync_service.sync_single_media(media_id)
            mark_search_projection_repair_completed(
                db=db,
                resource_type=SearchProjectionRepairResourceType.MEDIA,
                resource_id=media_id,
            )
        for episode_id in result.episode_ids:
            if should_remove_episodes:
                sync_service.delete_episode(episode_id)
            else:
                sync_service.sync_single_episode(episode_id)
            mark_search_projection_repair_completed(
                db=db,
                resource_type=SearchProjectionRepairResourceType.EPISODE,
                resource_id=episode_id,
            )
    except Exception as error:
        for media_id in result.media_ids:
            ensure_search_projection_repair(
                db=db,
                resource_type=SearchProjectionRepairResourceType.MEDIA,
                resource_id=media_id,
                reason=SearchProjectionRepairReason.TAKEDOWN_SUPPRESSION,
                status=SearchProjectionRepairStatus.FAILED,
                error_message=str(error),
            )
        for episode_id in result.episode_ids:
            ensure_search_projection_repair(
                db=db,
                resource_type=SearchProjectionRepairResourceType.EPISODE,
                resource_id=episode_id,
                reason=SearchProjectionRepairReason.TAKEDOWN_SUPPRESSION,
                status=SearchProjectionRepairStatus.FAILED,
                error_message=str(error),
            )
        logger.warning(
            "takedown_projection_sync_failed",
            takedown_request_id=request.id,
            error=str(error),
        )


def _to_ops_pipeline_schedule_summary(
    *,
    schedule: PipelineScheduleSummaryData,
) -> OpsPipelineScheduleSummary:
    """Convert a scheduled pipeline summary to the v2 ops schema."""
    return OpsPipelineScheduleSummary(
        id=encode_pipeline_schedule_id(schedule_id=schedule.id),
        schedule_key=schedule.schedule_key,
        task_kind=schedule.task_kind.value,
        interval_minutes=schedule.interval_minutes,
        enabled=schedule.enabled,
        metadata=schedule.metadata_json,
        last_scheduled_at=schedule.last_scheduled_at,
        next_due_at=schedule.next_due_at,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


def _to_ops_scheduled_work_item_summary(
    *,
    item: ScheduledWorkItemSummaryData,
) -> OpsScheduledWorkItemSummary:
    """Convert a scheduled work item summary to the v2 ops schema."""
    return OpsScheduledWorkItemSummary(
        id=encode_scheduled_work_id(work_item_id=item.id),
        schedule_id=encode_pipeline_schedule_id(schedule_id=item.schedule_id),
        ingestion_run_id=(
            encode_ingestion_run_id(ingestion_run_id=item.ingestion_run_id)
            if item.ingestion_run_id is not None
            else None
        ),
        schedule_key=item.schedule_key,
        work_key=item.work_key,
        task_kind=item.task_kind.value,
        status=item.status.value,
        due_at=item.due_at,
        interval_minutes=item.interval_minutes,
        metadata=item.metadata_json,
        error_message=item.error_message,
        created_at=item.created_at,
        started_at=item.started_at,
        completed_at=item.completed_at,
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


def _to_ops_media_merge_preview_response(
    *,
    preview: OpsMediaMergePreviewData,
) -> OpsMediaMergePreviewResponse:
    """Convert a media merge preview to the v2 ops schema.

    Args:
        preview: Shared media merge preview.

    Returns:
        Media merge preview response payload.
    """
    return OpsMediaMergePreviewResponse(
        source=_to_ops_merged_media_summary(media=preview.source),
        target=_to_ops_merged_media_summary(media=preview.target),
        field_changes=[
            OpsMediaMergeFieldChange(
                field=change.field,
                source_value=change.source_value,
                target_value=change.target_value,
                merged_value=change.merged_value,
            )
            for change in preview.field_changes
        ],
        alias_additions=[
            OpsMediaMergeAliasAddition(
                alias=alias.alias,
                normalized_alias=alias.normalized_alias,
                source=alias.source.value,
            )
            for alias in preview.alias_additions
        ],
        mentions_to_move=preview.mentions_to_move,
    )


def _to_ops_media_detail_response(
    *,
    detail: OpsMediaDetailData,
) -> OpsMediaDetailResponse:
    """Convert managed media detail to the v2 ops schema.

    Args:
        detail: Shared canonical media detail.

    Returns:
        Ops media detail response payload.
    """
    return OpsMediaDetailResponse(
        media=_to_ops_merged_media_summary(media=detail.summary),
        google_books_id=detail.google_books_id,
        open_library_id=detail.open_library_id,
        imdb_id=detail.imdb_id,
        tmdb_id=detail.tmdb_id,
        wikipedia_id=detail.wikipedia_id,
        pubmed_id=detail.pubmed_id,
        doi=detail.doi,
        semantic_scholar_id=detail.semantic_scholar_id,
        metadata_json=detail.metadata_json,
        verification_sources=detail.verification_sources,
        aliases=[
            OpsMediaAlias(
                alias=alias.alias,
                normalized_alias=alias.normalized_alias,
                source=alias.source,
                is_primary=alias.is_primary,
            )
            for alias in detail.aliases
        ],
        external_refs=[
            OpsMediaExternalRef(
                source=reference.source,
                external_id=reference.external_id,
                url=reference.url,
                label=reference.label,
                description=reference.description,
            )
            for reference in detail.external_refs
        ],
        relations=[
            OpsMediaRelation(
                direction=relation.direction,
                relation_type=relation.relation_type,
                related_media=_to_ops_merged_media_summary(
                    media=relation.related_media
                ),
                source=relation.source,
                confidence=relation.confidence,
            )
            for relation in detail.relations
        ],
        mentions=[
            OpsMediaMention(
                id=encode_mention_id(mention_id=mention.id),
                episode_id=encode_episode_id(episode_id=mention.episode_id),
                episode_title=mention.episode_title,
                timestamp_seconds=mention.timestamp_seconds,
                context=mention.context,
                confidence=mention.confidence,
            )
            for mention in detail.mentions
        ],
    )


def _to_ops_media_split_response(
    *,
    result: OpsMediaSplitResultData,
) -> OpsMediaSplitResponse:
    """Convert canonical media split recovery to the v2 ops schema.

    Args:
        result: Shared media split result.

    Returns:
        Split recovery response payload.
    """
    return OpsMediaSplitResponse(
        source=_to_ops_media_detail_response(detail=result.source),
        created=_to_ops_media_detail_response(detail=result.created),
        mentions_moved=result.mentions_moved,
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
    if resource_type == "transcript":
        return encode_transcript_id(transcript_id=resource_id)

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


def _to_ops_review_split_response(
    *,
    result: ReviewSplitResultData,
) -> OpsReviewSplitResponse:
    """Convert split decision data to the v2 ops response schema."""
    return OpsReviewSplitResponse(
        original=_to_ops_review_queue_item(item=result.original),
        items=[_to_ops_review_queue_item(item=item) for item in result.items],
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


def _to_ops_retention_sampling_report(
    *,
    report: RetentionSamplingReportData,
) -> OpsRetentionSamplingReportResponse:
    """Convert calibration corpus coverage to the v2 ops schema."""
    return OpsRetentionSamplingReportResponse(
        policy_version=report.policy_version,
        sample_rate=report.sample_rate,
        eligible_count=report.eligible_count,
        sampled_count=report.sampled_count,
        target_count=report.target_count,
        strata=[
            OpsRetentionSamplingStratum(
                source=item.source,
                topic=item.topic,
                confidence_band=item.confidence_band,
                age_bucket=item.age_bucket,
                eligible_count=item.eligible_count,
                sampled_count=item.sampled_count,
                target_count=item.target_count,
            )
            for item in report.strata
        ],
    )


def _to_ops_transcript_retention_summary(
    *,
    transcript: OpsTranscriptRetentionData,
) -> OpsTranscriptRetentionSummary:
    """Convert a transcript lifecycle state to the v2 ops schema."""
    return OpsTranscriptRetentionSummary(
        id=encode_transcript_id(transcript_id=transcript.id),
        episode_id=encode_episode_id(episode_id=transcript.episode_id),
        episode_title=transcript.episode_title,
        podcast_name=transcript.podcast_name,
        provider=transcript.provider,
        fetched_at=transcript.fetched_at,
        tier=transcript.tier,
        policy_version=transcript.policy_version,
        retention_exempt_sample=transcript.retention_exempt_sample,
        source_retention_opt_out=transcript.source_retention_opt_out,
        purge_eligible_at=transcript.purge_eligible_at,
        purged_at=transcript.purged_at,
        has_raw_payload=transcript.has_raw_payload,
        has_stored_artifact=transcript.has_stored_artifact,
        digest_id=(
            encode_transcript_digest_id(digest_id=transcript.digest_id)
            if transcript.digest_id is not None
            else None
        ),
    )


def _to_ops_transcript_retention_preview(
    *,
    preview: OpsTranscriptRetentionPreviewData,
) -> OpsTranscriptRetentionPreviewResponse:
    """Convert an evaluated lifecycle gate to the v2 ops schema."""
    return OpsTranscriptRetentionPreviewResponse(
        transcript=_to_ops_transcript_retention_summary(transcript=preview.transcript),
        proposed_tier=preview.decision.tier.value,
        purge_eligible=preview.decision.purge_eligible
        and preview.derivative_coverage_ready,
        purge_blockers=[item.value for item in preview.decision.purge_blockers],
        extraction_confidence=preview.extraction_confidence,
        derivative_coverage_ready=preview.derivative_coverage_ready,
        missing_query_classes=list(preview.missing_query_classes),
    )


def _retention_policy_from_request(
    *,
    payload: OpsTranscriptRetentionPolicyRequest,
) -> TranscriptRetentionPolicy:
    """Build a validated retention policy from operator inputs."""
    try:
        return TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=payload.hot_days),
            warm_retention_period=timedelta(days=payload.warm_days),
            min_purge_confidence=payload.min_purge_confidence,
            retention_sample_rate=0,
            sample_version=payload.policy_version,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def _encode_takedown_subject(*, request: TakedownRequest) -> str:
    """Encode a takedown target using its catalog resource identifier."""
    if request.subject_type == TakedownSubjectType.PODCAST.value:
        return encode_podcast_id(podcast_id=request.subject_id)
    if request.subject_type == TakedownSubjectType.EPISODE.value:
        return encode_episode_id(episode_id=request.subject_id)
    return encode_mention_id(mention_id=request.subject_id)


def _to_ops_takedown_request(request: TakedownRequest) -> OpsTakedownRequestSummary:
    """Convert a privileged takedown case to the ops schema."""
    return OpsTakedownRequestSummary(
        id=encode_takedown_request_id(takedown_request_id=request.id),
        subject_type=request.subject_type,
        subject_id=_encode_takedown_subject(request=request),
        requester_type=request.requester_type,
        requester_name=request.requester_name,
        requester_email=request.requester_email,
        basis=request.basis,
        requested_actions=request.requested_actions_json,
        status=request.status,
        decision_note=request.decision_note,
        decided_by=request.decided_by,
        decided_at=request.decided_at,
        submitted_at=request.created_at,
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


@router.get("/metrics", response_model=OpsOperationalMetricsResponse)
def get_ops_operational_metrics(
    db: Session = Depends(get_db),
) -> OpsOperationalMetricsResponse:
    """Get review, projection-lag, and account-delivery health metrics."""
    metrics = get_operational_metrics(db=db)
    return OpsOperationalMetricsResponse(
        measured_at=metrics.measured_at,
        review=OpsReviewThroughputMetrics(
            pending_items=metrics.review.pending_items,
            decisions_last_24h=metrics.review.decisions_last_24h,
            median_decision_minutes_last_24h=(
                metrics.review.median_decision_minutes_last_24h
            ),
        ),
        projection=OpsProjectionLagMetrics(
            pending_repairs=metrics.projection.pending_repairs,
            failed_repairs=metrics.projection.failed_repairs,
            oldest_pending_age_seconds=metrics.projection.oldest_pending_age_seconds,
        ),
        alerts=OpsAlertDeliveryMetrics(
            generated_events_last_24h=metrics.alerts.generated_events_last_24h,
            delivered_digests_last_24h=metrics.alerts.delivered_digests_last_24h,
            delivered_events_last_24h=metrics.alerts.delivered_events_last_24h,
            pending_events=metrics.alerts.pending_events,
        ),
    )


@router.get("/alerts", response_model=OpsOperationalAlertResponse)
def get_ops_operational_alerts(
    db: Session = Depends(get_db),
) -> OpsOperationalAlertResponse:
    """Get configured operational threshold breaches and playbook keys."""
    settings = get_settings()
    metrics = get_operational_metrics(db=db)
    alerts = evaluate_operational_alerts(
        metrics=metrics,
        thresholds=OperationalAlertThresholdsData(
            review_pending=settings.ops_review_pending_alert_threshold,
            projection_pending=settings.ops_projection_pending_alert_threshold,
            projection_oldest_pending_minutes=(
                settings.ops_projection_oldest_pending_minutes
            ),
            alert_delivery_pending=settings.ops_alert_delivery_pending_threshold,
        ),
    )
    return OpsOperationalAlertResponse(
        measured_at=metrics.measured_at,
        alerts=[
            OpsOperationalAlert(
                key=alert.key,
                severity=alert.severity,
                title=alert.title,
                message=alert.message,
                current_value=alert.current_value,
                threshold=alert.threshold,
                playbook_slug=alert.playbook_slug,
            )
            for alert in alerts
        ],
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
    "/review-queue/{review_item_id}/split",
    response_model=OpsReviewSplitResponse,
)
def split_ops_review_item(
    review_item_id: str,
    payload: OpsReviewSplitRequest,
    db: Session = Depends(get_db),
) -> OpsReviewSplitResponse:
    """Split an ambiguous review candidate into replacement queue items."""
    try:
        internal_review_item_id = decode_review_item_id(review_item_id=review_item_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Review item not found") from error

    try:
        result = split_review_queue_item(
            db=db,
            review_item_id=internal_review_item_id,
            payload=ReviewSplitInputData(
                actor_name=payload.actor_name,
                note=payload.note,
                candidates=tuple(
                    ReviewSplitCandidateInputData(
                        media_type=item.type.value,
                        raw_title=item.raw_title,
                        suggested_author=item.suggested_author,
                    )
                    for item in payload.candidates
                ),
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error

    if result is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Review item not found")

    record_audit_log(
        db=db,
        action=AuditAction.SPLIT_REVIEW_ITEM,
        resource_type="review_item",
        resource_id=result.original.id,
        actor_name=payload.actor_name,
        summary=f"Split review item for {result.original.candidate.raw_title}",
        metadata_json={
            "candidate_id": result.original.candidate.id,
            "replacement_candidate_ids": [item.candidate.id for item in result.items],
        },
    )
    db.commit()
    return _to_ops_review_split_response(result=result)


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


@router.get("/media/{media_id}", response_model=OpsMediaDetailResponse)
def get_ops_media_detail_route(
    media_id: str,
    db: Session = Depends(get_db),
) -> OpsMediaDetailResponse:
    """Get editable canonical media detail for operator management.

    Args:
        media_id: Opaque media identifier.
        db: Database session.

    Returns:
        Managed media detail with aliases, references, and relations.

    Raises:
        HTTPException: If the media record does not exist.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    detail = get_ops_media_detail(db=db, media_id=internal_media_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Media not found")
    return _to_ops_media_detail_response(detail=detail)


@router.patch("/media/{media_id}", response_model=OpsMediaDetailResponse)
def update_ops_media_route(
    media_id: str,
    payload: OpsMediaUpdateRequest,
    db: Session = Depends(get_db),
) -> OpsMediaDetailResponse:
    """Correct canonical media metadata through the ops console.

    Args:
        media_id: Opaque media identifier.
        payload: Partial metadata correction.
        db: Database session.

    Returns:
        Updated managed media detail.

    Raises:
        HTTPException: If the record is missing or the update is invalid.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    updated_fields = payload.model_fields_set - {"actor_name", "note"}
    if not updated_fields:
        raise HTTPException(
            status_code=400,
            detail="At least one media field is required",
        )
    try:
        detail = update_ops_media(
            db=db,
            media_id=internal_media_id,
            payload=UpdateOpsMediaInputData(
                provided_fields=frozenset(updated_fields),
                type=payload.type,
                title=payload.title,
                author=payload.author,
                cover_url=payload.cover_url,
                year=payload.year,
                description=payload.description,
                google_books_id=payload.google_books_id,
                open_library_id=payload.open_library_id,
                imdb_id=payload.imdb_id,
                tmdb_id=payload.tmdb_id,
                wikipedia_id=payload.wikipedia_id,
                pubmed_id=payload.pubmed_id,
                doi=payload.doi,
                semantic_scholar_id=payload.semantic_scholar_id,
                metadata_json=payload.metadata_json,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error
    if detail is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Media not found")

    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_MEDIA,
        resource_type="media",
        resource_id=internal_media_id,
        actor_name=payload.actor_name,
        summary=f"Updated media {detail.summary.title}",
        metadata_json={
            "updated_fields": sorted(updated_fields),
            "note": payload.note,
        },
    )
    db.commit()
    _sync_media_projection_update(
        db=db,
        media_ids=(internal_media_id,),
        reason=SearchProjectionRepairReason.MEDIA_UPDATE,
    )
    return _to_ops_media_detail_response(detail=detail)


@router.post("/media/{media_id}/aliases", response_model=OpsMediaDetailResponse)
def add_ops_media_alias_route(
    media_id: str,
    payload: OpsMediaAliasRequest,
    db: Session = Depends(get_db),
) -> OpsMediaDetailResponse:
    """Attach a manual alias to a canonical media record.

    Args:
        media_id: Opaque media identifier.
        payload: Alias input and audit context.
        db: Database session.

    Returns:
        Updated managed media detail.

    Raises:
        HTTPException: If the media record does not exist.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    detail = add_ops_media_alias(
        db=db,
        media_id=internal_media_id,
        alias=payload.alias,
    )
    if detail is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Media not found")
    record_audit_log(
        db=db,
        action=AuditAction.ADD_MEDIA_ALIAS,
        resource_type="media",
        resource_id=internal_media_id,
        actor_name=payload.actor_name,
        summary=f"Added media alias {payload.alias.strip()}",
        metadata_json={"alias": payload.alias.strip(), "note": payload.note},
    )
    db.commit()
    return _to_ops_media_detail_response(detail=detail)


@router.post("/media/{media_id}/external-refs", response_model=OpsMediaDetailResponse)
def upsert_ops_media_external_ref_route(
    media_id: str,
    payload: OpsMediaExternalRefRequest,
    db: Session = Depends(get_db),
) -> OpsMediaDetailResponse:
    """Create or update an operator-managed external reference.

    Args:
        media_id: Opaque media identifier.
        payload: External reference input and audit context.
        db: Database session.

    Returns:
        Updated managed media detail.

    Raises:
        HTTPException: If the media record does not exist.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    detail = upsert_ops_media_external_ref(
        db=db,
        media_id=internal_media_id,
        payload=UpsertOpsMediaExternalRefInputData(
            source=payload.source,
            external_id=payload.external_id,
            url=payload.url,
            label=payload.label,
            description=payload.description,
        ),
    )
    if detail is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Media not found")
    record_audit_log(
        db=db,
        action=AuditAction.UPSERT_MEDIA_EXTERNAL_REF,
        resource_type="media",
        resource_id=internal_media_id,
        actor_name=payload.actor_name,
        summary=f"Updated media reference {payload.source.value}:{payload.external_id}",
        metadata_json={
            "source": payload.source.value,
            "external_id": payload.external_id,
            "note": payload.note,
        },
    )
    db.commit()
    return _to_ops_media_detail_response(detail=detail)


@router.post("/media/{media_id}/split", response_model=OpsMediaSplitResponse)
def split_ops_media_route(
    media_id: str,
    payload: OpsMediaSplitRequest,
    db: Session = Depends(get_db),
) -> OpsMediaSplitResponse:
    """Recover selected mentions into a newly separated canonical record.

    Args:
        media_id: Opaque media identifier containing incorrectly merged mentions.
        payload: Replacement record and selected mention identifiers.
        db: Database session.

    Returns:
        Updated source record and newly created split record.

    Raises:
        HTTPException: If identifiers are invalid or selected mentions do not belong
            to the source record.
    """
    try:
        internal_media_id = decode_media_id(media_id=media_id)
        mention_ids = tuple(
            decode_mention_id(mention_id=mention_id)
            for mention_id in payload.mention_ids
        )
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail="Media or mention not found",
        ) from error

    try:
        result = split_ops_media(
            db=db,
            media_id=internal_media_id,
            payload=SplitOpsMediaInputData(
                mention_ids=mention_ids,
                type=payload.type,
                title=payload.title,
                author=payload.author,
                description=payload.description,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error
    if result is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Media not found")

    record_audit_log(
        db=db,
        action=AuditAction.SPLIT_MEDIA,
        resource_type="media",
        resource_id=internal_media_id,
        actor_name=payload.actor_name,
        summary=(
            f"Split {result.mentions_moved} mentions from "
            f"{result.source.summary.title} into {result.created.summary.title}"
        ),
        metadata_json={
            "created_media_id": result.created.summary.id,
            "mention_ids": list(mention_ids),
            "note": payload.note,
        },
    )
    db.commit()
    _sync_media_projection_update(
        db=db,
        media_ids=(result.source.summary.id, result.created.summary.id),
        reason=SearchProjectionRepairReason.MEDIA_SPLIT,
    )
    return _to_ops_media_split_response(result=result)


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


@router.get(
    "/media/{media_id}/merge-preview",
    response_model=OpsMediaMergePreviewResponse,
)
def preview_ops_media_merge_route(
    media_id: str,
    target_id: str = Query(...),
    db: Session = Depends(get_db),
) -> OpsMediaMergePreviewResponse:
    """Preview one media merge without mutating catalog records.

    Args:
        media_id: Opaque media identifier to merge from.
        target_id: Opaque target media identifier.
        db: Database session.

    Returns:
        Merge preview for operator review.

    Raises:
        HTTPException: If either media record is missing or the merge is invalid.
    """
    try:
        source_media_id = decode_media_id(media_id=media_id)
        target_media_id = decode_media_id(media_id=target_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Media not found") from error

    try:
        preview = preview_ops_media_merge(
            db=db,
            source_media_id=source_media_id,
            target_media_id=target_media_id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if preview is None:
        raise HTTPException(status_code=404, detail="Media not found")

    return _to_ops_media_merge_preview_response(preview=preview)


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


@router.get(
    "/retention/sampling",
    response_model=OpsRetentionSamplingReportResponse,
)
def get_ops_retention_sampling(
    db: Session = Depends(get_db),
) -> OpsRetentionSamplingReportResponse:
    """Get coverage of the currently assigned permanent calibration corpus."""
    return _to_ops_retention_sampling_report(
        report=get_retention_sampling_report(db=db),
    )


@router.post(
    "/retention/sampling/recalculate",
    response_model=OpsRetentionSamplingReportResponse,
)
def recalculate_ops_retention_sampling(
    payload: OpsRetentionSamplingRecalculateRequest,
    db: Session = Depends(get_db),
) -> OpsRetentionSamplingReportResponse:
    """Apply a versioned sampling policy and record the operator action."""
    policy = TranscriptRetentionPolicy(
        retention_sample_rate=payload.sample_rate,
        sample_version=payload.policy_version,
    )
    report = recalculate_retention_sample(db=db, policy=policy)
    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_RETENTION_SAMPLING,
        resource_type="retention_sampling_policy",
        resource_identifier=payload.policy_version,
        actor_name=payload.actor_name,
        summary=f"Recalculated retention sample with {payload.policy_version}",
        metadata_json={
            "sample_rate": payload.sample_rate,
            "eligible_count": report.eligible_count,
            "sampled_count": report.sampled_count,
            "note": payload.note,
        },
    )
    db.commit()
    return _to_ops_retention_sampling_report(report=report)


@router.get(
    "/retention/transcripts",
    response_model=OpsTranscriptRetentionListResponse,
)
def get_ops_transcript_retention(
    limit: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
) -> OpsTranscriptRetentionListResponse:
    """List transcript lifecycle records for operator review."""
    return OpsTranscriptRetentionListResponse(
        items=[
            _to_ops_transcript_retention_summary(transcript=item)
            for item in list_ops_transcript_retention(db=db, limit=limit)
        ],
    )


@router.post(
    "/retention/transcripts/{transcript_id}/preview",
    response_model=OpsTranscriptRetentionPreviewResponse,
)
def preview_ops_transcript_retention_route(
    transcript_id: str,
    payload: OpsTranscriptRetentionPolicyRequest,
    db: Session = Depends(get_db),
) -> OpsTranscriptRetentionPreviewResponse:
    """Dry-run a transcript lifecycle decision without mutating storage."""
    try:
        internal_id = decode_transcript_id(transcript_id=transcript_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Transcript not found") from error
    preview = preview_ops_transcript_retention(
        db=db,
        transcript_id=internal_id,
        policy=_retention_policy_from_request(payload=payload),
        source_retention_opt_out=payload.source_retention_opt_out,
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return _to_ops_transcript_retention_preview(preview=preview)


@router.post(
    "/retention/transcripts/{transcript_id}/evaluate",
    response_model=OpsTranscriptRetentionPreviewResponse,
)
def evaluate_ops_transcript_retention_route(
    transcript_id: str,
    payload: OpsTranscriptRetentionPolicyRequest,
    db: Session = Depends(get_db),
) -> OpsTranscriptRetentionPreviewResponse:
    """Persist an audited transcript lifecycle evaluation."""
    try:
        internal_id = decode_transcript_id(transcript_id=transcript_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Transcript not found") from error
    policy = _retention_policy_from_request(payload=payload)
    preview = apply_ops_transcript_retention(
        db=db,
        transcript_id=internal_id,
        policy=policy,
        source_retention_opt_out=payload.source_retention_opt_out,
    )
    if preview is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Transcript not found")
    transcript_model = db.query(Transcript).filter(Transcript.id == internal_id).one()
    upsert_transcript_source_retention_policy(
        db=db,
        transcript=transcript_model,
        policy=policy,
        source_retention_opt_out=payload.source_retention_opt_out,
    )
    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_TRANSCRIPT_RETENTION_POLICY,
        resource_type="transcript",
        resource_id=internal_id,
        actor_name=payload.actor_name,
        summary=f"Saved transcript retention policy for {preview.transcript.provider}",
        metadata_json={
            "policy_version": payload.policy_version,
            "source_retention_opt_out": payload.source_retention_opt_out,
            "note": payload.note,
        },
    )
    record_audit_log(
        db=db,
        action=AuditAction.EVALUATE_TRANSCRIPT_RETENTION,
        resource_type="transcript",
        resource_id=internal_id,
        actor_name=payload.actor_name,
        summary=f"Evaluated transcript retention as {preview.decision.tier.value}",
        metadata_json={
            "policy_version": payload.policy_version,
            "purge_eligible": preview.decision.purge_eligible,
            "derivative_coverage_ready": preview.derivative_coverage_ready,
            "note": payload.note,
        },
    )
    db.commit()
    return _to_ops_transcript_retention_preview(preview=preview)


@router.post(
    "/retention/transcripts/{transcript_id}/purge",
    response_model=OpsTranscriptPurgeResponse,
)
def purge_ops_transcript_route(
    transcript_id: str,
    payload: OpsTranscriptRetentionPolicyRequest,
    db: Session = Depends(get_db),
    artifact_store: TranscriptArtifactStore | None = Depends(
        get_ops_transcript_artifact_store
    ),
) -> OpsTranscriptPurgeResponse:
    """Delete eligible raw transcript data while retaining a durable digest."""
    try:
        internal_id = decode_transcript_id(transcript_id=transcript_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Transcript not found") from error
    try:
        result = purge_ops_transcript(
            db=db,
            transcript_id=internal_id,
            artifact_store=artifact_store,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if result is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Transcript not found")
    record_audit_log(
        db=db,
        action=AuditAction.PURGE_TRANSCRIPT,
        resource_type="transcript",
        resource_id=internal_id,
        actor_name=payload.actor_name,
        summary=f"Purged raw transcript for {result.transcript.episode_title}",
        metadata_json={
            "digest_id": result.digest.id,
            "source_text_hash": result.digest.source_text_hash,
            "note": payload.note,
        },
    )
    db.commit()
    return OpsTranscriptPurgeResponse(
        transcript=_to_ops_transcript_retention_summary(transcript=result.transcript),
        digest=OpsTranscriptDigestResponse(
            id=encode_transcript_digest_id(digest_id=result.digest.id),
            transcript_id=encode_transcript_id(
                transcript_id=result.digest.transcript_id
            ),
            source_text_hash=result.digest.source_text_hash,
            provider=result.digest.provider,
            policy_version=result.digest.policy_version,
            summary_text=result.digest.summary_text,
            extraction_versions=result.digest.extraction_versions_json or [],
            purged_at=result.digest.purged_at,
        ),
    )


@router.post(
    "/retention/transcripts/{transcript_id}/reacquire",
    response_model=OpsTranscriptReacquireResponse,
)
def reacquire_ops_transcript_route(
    transcript_id: str,
    payload: OpsTranscriptReacquireRequest,
    db: Session = Depends(get_db),
    artifact_store: TranscriptArtifactStore | None = Depends(
        get_ops_transcript_artifact_store
    ),
    acquirer: TranscriptAcquirer = Depends(get_ops_transcript_acquirer),
) -> OpsTranscriptReacquireResponse:
    """Re-acquire a purged transcript as a fresh encrypted hot asset."""
    try:
        internal_id = decode_transcript_id(transcript_id=transcript_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Transcript not found") from error
    try:
        result: OpsTranscriptReacquisitionResultData | None = reacquire_ops_transcript(
            db=db,
            transcript_id=internal_id,
            acquirer=acquirer,
            artifact_store=artifact_store,
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        acquirer.close()
    if result is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Transcript not found")
    record_audit_log(
        db=db,
        action=AuditAction.REACQUIRE_TRANSCRIPT,
        resource_type="transcript",
        resource_id=result.transcript.id,
        actor_name=payload.actor_name,
        summary=f"Re-acquired raw transcript for {result.transcript.episode_title}",
        metadata_json={
            "prior_transcript_id": internal_id,
            "prior_digest_id": result.prior_digest.id,
            "artifact_id": result.artifact.id,
            "note": payload.note,
        },
    )
    db.commit()
    return OpsTranscriptReacquireResponse(
        transcript=_to_ops_transcript_retention_summary(transcript=result.transcript),
        artifact_id=encode_transcript_artifact_id(artifact_id=result.artifact.id),
        prior_digest_id=encode_transcript_digest_id(digest_id=result.prior_digest.id),
    )


@router.get("/takedown-requests", response_model=OpsTakedownRequestListResponse)
def get_ops_takedown_requests(
    request_status: TakedownRequestStatus | None = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> OpsTakedownRequestListResponse:
    """List rights-holder and creator takedown cases for operator review."""
    return OpsTakedownRequestListResponse(
        items=[
            _to_ops_takedown_request(request)
            for request in list_takedown_requests(
                db=db,
                status=request_status,
                limit=limit,
            )
        ],
    )


@router.post(
    "/takedown-requests/{takedown_request_id}/decision",
    response_model=OpsTakedownRequestSummary,
)
def decide_ops_takedown_request(
    takedown_request_id: str,
    payload: OpsTakedownDecisionRequest,
    db: Session = Depends(get_db),
    artifact_store: TranscriptArtifactStore | None = Depends(
        get_ops_transcript_artifact_store
    ),
) -> OpsTakedownRequestSummary:
    """Record a decision and execute approved takedown actions."""
    try:
        internal_id = decode_takedown_request_id(
            takedown_request_id=takedown_request_id,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=404,
            detail="Takedown request not found",
        ) from error
    try:
        request = decide_takedown_request(
            db=db,
            request_id=internal_id,
            payload=TakedownDecisionInputData(
                status=TakedownRequestStatus(payload.status),
                actor_name=payload.actor_name,
                note=payload.note,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if request is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="Takedown request not found")
    execution: TakedownExecutionResultData | None = None
    if request.status == TakedownRequestStatus.APPROVED.value:
        try:
            execution = execute_approved_takedown_request(
                db=db,
                request=request,
                artifact_store=artifact_store,
            )
        except ValueError as error:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        _sync_takedown_projection(db=db, request=request, result=execution)
    record_audit_log(
        db=db,
        action=AuditAction.DECIDE_TAKEDOWN_REQUEST,
        resource_type="takedown_request",
        resource_id=request.id,
        actor_name=payload.actor_name,
        summary=f"{payload.status.capitalize()} takedown request",
        metadata_json={
            "subject_type": request.subject_type,
            "subject_id": request.subject_id,
            "requested_actions": request.requested_actions_json,
            "note": payload.note,
            "execution": (
                {
                    "transcripts_suppressed": execution.transcripts_suppressed,
                    "derivatives_suppressed": execution.derivatives_suppressed,
                    "mentions_unpublished": execution.mentions_unpublished,
                    "source_opt_outs_registered": execution.source_opt_outs_registered,
                }
                if execution is not None
                else None
            ),
        },
    )
    db.commit()
    return _to_ops_takedown_request(request)


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


@router.post("/search/reindex", response_model=OpsSearchReindexResponse)
def queue_ops_search_reindex_route(
    payload: OpsSearchReindexRequest,
    db: Session = Depends(get_db),
) -> OpsSearchReindexResponse:
    """Queue scoped replay-safe search projection rebuild work."""
    podcast_id: int | None = None
    if payload.podcast_id is not None:
        try:
            podcast_id = decode_podcast_id(podcast_id=payload.podcast_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Podcast not found") from error

    result = queue_ops_search_reindex(
        db=db,
        payload=OpsSearchReindexInputData(
            resource_type=payload.resource_type,
            podcast_id=podcast_id,
            media_type=payload.media_type,
            created_after=payload.created_after,
        ),
    )
    record_audit_log(
        db=db,
        action=AuditAction.REINDEX_SEARCH,
        resource_type="search_projection",
        actor_name=payload.actor_name,
        summary=f"Queued {result.total_queued} search projection repairs",
        metadata_json={
            "resource_type": payload.resource_type,
            "podcast_id": podcast_id,
            "media_type": (
                payload.media_type.value if payload.media_type is not None else None
            ),
            "created_after": (
                payload.created_after.isoformat()
                if payload.created_after is not None
                else None
            ),
            "note": payload.note,
        },
    )
    db.commit()
    return OpsSearchReindexResponse(
        media_queued=result.media_queued,
        episodes_queued=result.episodes_queued,
        total_queued=result.total_queued,
    )


@router.post("/search/tuning/preview", response_model=OpsSearchTuningPreviewResponse)
def preview_ops_search_tuning(
    payload: OpsSearchTuningPreviewRequest,
) -> OpsSearchTuningPreviewResponse:
    """Preview proposed tuning with a baseline sample query."""
    proposed_settings: dict[str, object] = {"synonyms": payload.synonyms}
    if payload.ranking_rules is not None:
        proposed_settings["rankingRules"] = payload.ranking_rules
    sample = get_search_client().search(payload.index, payload.query, limit=5)
    return OpsSearchTuningPreviewResponse(
        index=payload.index,
        query=payload.query,
        sample_hits=sample.get("hits", []),
        proposed_settings=proposed_settings,
    )


@router.post("/search/tuning", response_model=OpsSearchTuningApplyResponse)
def apply_ops_search_tuning(
    payload: OpsSearchTuningApplyRequest,
    db: Session = Depends(get_db),
) -> OpsSearchTuningApplyResponse:
    """Apply reviewed synonym and ranking settings to one search index."""
    proposed_settings: dict[str, object] = {"synonyms": payload.synonyms}
    if payload.ranking_rules is not None:
        proposed_settings["rankingRules"] = payload.ranking_rules
    result = get_search_client().update_index_settings(payload.index, proposed_settings)
    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_SEARCH_TUNING,
        resource_type="search_projection",
        resource_identifier=payload.index,
        actor_name=payload.actor_name,
        summary=f"Updated search tuning for {payload.index}",
        metadata_json={
            "settings": proposed_settings,
            "query": payload.query,
            "note": payload.note,
        },
    )
    db.commit()
    return OpsSearchTuningApplyResponse(
        index=payload.index,
        status=str(result.get("status", "enqueued")),
        task_uid=result.get("task_uid"),
    )


@router.get("/search/analytics", response_model=OpsSearchAnalyticsResponse)
def get_ops_search_analytics(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> OpsSearchAnalyticsResponse:
    """Return anonymous public search signals for relevance review."""
    summary = get_search_analytics_summary(db=db, limit=limit)
    return OpsSearchAnalyticsResponse(
        searches=summary.searches,
        zero_result_searches=summary.zero_result_searches,
        selections=summary.selections,
        queries=[
            OpsSearchQueryMetric(
                query=item.query,
                searches=item.searches,
                zero_result_searches=item.zero_result_searches,
                selections=item.selections,
            )
            for item in summary.queries
        ],
    )


@router.get("/scheduled-work", response_model=OpsScheduledWorkResponse)
def get_ops_scheduled_work(
    limit: int = Query(50, ge=1, le=100),
    status: ScheduledWorkStatus | None = Query(None),
    db: Session = Depends(get_db),
) -> OpsScheduledWorkResponse:
    """List recurring schedules and recent scheduled work for ops views.

    Args:
        limit: Maximum number of work items to return.
        status: Optional scheduled work status filter.
        db: Database session.

    Returns:
        Scheduled work response.
    """
    return OpsScheduledWorkResponse(
        schedules=[
            _to_ops_pipeline_schedule_summary(schedule=schedule)
            for schedule in list_pipeline_schedules(db=db)
        ],
        work_items=[
            _to_ops_scheduled_work_item_summary(item=item)
            for item in list_scheduled_work_items(
                db=db,
                limit=limit,
                status=status,
            )
        ],
    )


@router.post("/scheduled-work/plan", response_model=list[OpsScheduledWorkItemSummary])
def plan_ops_scheduled_work(
    db: Session = Depends(get_db),
) -> list[OpsScheduledWorkItemSummary]:
    """Plan currently due recurring work items.

    Args:
        db: Database session.

    Returns:
        Newly planned scheduled work items.
    """
    items = plan_due_scheduled_work(db=db)
    db.commit()
    return [_to_ops_scheduled_work_item_summary(item=item) for item in items]
