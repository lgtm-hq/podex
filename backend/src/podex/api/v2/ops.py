"""Ops console endpoints for operators.

The whole surface is disabled until ``PODEX_OPS_API_KEY`` is configured;
requests must present the key in the ``X-Ops-Key`` header. Mutations are
recorded in the immutable audit log.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

from podex.api.deps import AppSettings, DbSession
from podex.models import (
    AuditAction,
    PodcastStatus,
    TakedownRequestStatus,
)
from podex.schemas.ops import (
    OpsAuditLogEntryRead,
    OpsAuditLogListRead,
    OpsMetricsRead,
    OpsOperationalAlertListRead,
    OpsOperationalAlertRead,
    OpsPipelineActivityRead,
    OpsPodcastCreateRequest,
    OpsPodcastListRead,
    OpsPodcastRead,
    OpsPodcastSortField,
    OpsPodcastUpdateRequest,
    OpsRetentionPreviewRead,
    OpsSortOrder,
    OpsTakedownDecisionRead,
    OpsTakedownDecisionRequest,
    OpsTakedownExecutionRead,
    OpsTakedownRequestRead,
    OpsTranscriptPurgeRead,
    OpsTranscriptRetentionRead,
    PodcastSourceType,
)
from podex.services.audit_log import list_audit_logs, record_audit_log
from podex.services.operational_alerts import (
    OperationalAlertThresholdsData,
    evaluate_operational_alerts,
)
from podex.services.ops_metrics import get_operational_metrics
from podex.services.ops_podcast_commands import (
    CreateOpsPodcastInputData,
    OpsPodcastSourceInputData,
    UpdateOpsPodcastInputData,
    archive_ops_podcast,
    create_ops_podcast,
    update_ops_podcast,
)
from podex.services.ops_podcast_queries import get_ops_podcast_by_id, list_ops_podcasts
from podex.services.ops_retention_commands import (
    OpsTranscriptRetentionPreviewData,
    apply_ops_transcript_retention,
    list_ops_transcript_retention,
    preview_ops_transcript_retention,
    purge_ops_transcript,
)
from podex.services.pipeline_queries import list_recent_ingestion_runs
from podex.services.takedown_requests import (
    TakedownDecisionInputData,
    decide_takedown_request,
    execute_approved_takedown_request,
    list_takedown_requests,
)
from podex.services.transcript_artifacts import build_transcript_artifact_store
from podex.services.transcript_retention import TranscriptRetentionPolicy

router = APIRouter(prefix="/ops", tags=["ops"])


def _require_ops_access(*, request: Request, settings: AppSettings) -> None:
    """Reject the request unless the configured ops key is presented."""
    if not settings.ops_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ops console is not configured",
        )
    if request.headers.get("X-Ops-Key") != settings.ops_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ops authentication required",
        )


def _to_retention_preview_read(
    *,
    preview: OpsTranscriptRetentionPreviewData,
) -> OpsRetentionPreviewRead:
    """Convert a retention preview service payload to its API schema."""
    return OpsRetentionPreviewRead(
        transcript=OpsTranscriptRetentionRead.model_validate(preview.transcript),
        decision={
            "tier": preview.decision.tier.value,
            "purge_eligible": preview.decision.purge_eligible,
            "purge_blockers": [
                blocker.value for blocker in preview.decision.purge_blockers
            ],
            "retention_suppressed": preview.decision.retention_suppressed,
            "age_days": preview.decision.age.days,
        },
        extraction_confidence=preview.extraction_confidence,
        derivative_coverage_ready=preview.derivative_coverage_ready,
        missing_query_classes=list(preview.missing_query_classes),
    )


def get_ops_metrics(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsMetricsRead:
    """Return operational dashboard metrics."""
    _require_ops_access(request=request, settings=settings)
    metrics = get_operational_metrics(db=db)
    db.commit()
    return OpsMetricsRead.model_validate(metrics, from_attributes=True)


def get_ops_operational_alerts(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsOperationalAlertListRead:
    """Return configured operational threshold breaches."""
    _require_ops_access(request=request, settings=settings)
    metrics = get_operational_metrics(db=db)
    alerts = evaluate_operational_alerts(
        metrics=metrics,
        thresholds=OperationalAlertThresholdsData(
            review_pending=settings.ops_review_pending_alert_threshold,
            alert_delivery_pending=settings.ops_alert_delivery_pending_threshold,
        ),
    )
    db.commit()
    return OpsOperationalAlertListRead(
        measured_at=metrics.measured_at,
        alerts=[
            OpsOperationalAlertRead.model_validate(alert, from_attributes=True)
            for alert in alerts
        ],
    )


def list_ops_podcasts_endpoint(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
    status_filter: Annotated[
        PodcastStatus | None,
        Query(alias="status"),
    ] = None,
    source: PodcastSourceType | None = None,
    sort: OpsPodcastSortField = "created_at",
    order: OpsSortOrder = "desc",
) -> OpsPodcastListRead:
    """List managed podcasts with filters and count sorting."""
    _require_ops_access(request=request, settings=settings)
    listed = list_ops_podcasts(
        db=db,
        page=page,
        per_page=per_page,
        status=status_filter,
        source=source,
        sort=sort,
        order=order,
    )
    db.commit()
    return OpsPodcastListRead.model_validate(listed, from_attributes=True)


def create_ops_podcast_endpoint(
    payload: OpsPodcastCreateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsPodcastRead:
    """Create a managed podcast and record the action."""
    _require_ops_access(request=request, settings=settings)
    try:
        created = create_ops_podcast(
            db=db,
            payload=CreateOpsPodcastInputData(
                name=payload.name,
                slug=payload.slug,
                status=payload.status,
                description=payload.description,
                cover_url=payload.cover_url,
                discovery_source=payload.discovery_source,
                sources=OpsPodcastSourceInputData(
                    **payload.sources.model_dump(),
                ),
            ),
        )
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Podcast slug already exists",
        ) from error
    record_audit_log(
        db=db,
        action=AuditAction.CREATE_PODCAST,
        resource_type="podcast",
        resource_id=created.id,
        resource_identifier=created.slug,
        summary=f"Created podcast {created.slug}",
    )
    db.commit()
    return OpsPodcastRead.model_validate(created, from_attributes=True)


def update_ops_podcast_endpoint(
    podcast_id: int,
    payload: OpsPodcastUpdateRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsPodcastRead:
    """Partially update a managed podcast and record the action."""
    _require_ops_access(request=request, settings=settings)
    provided = frozenset(payload.model_dump(exclude_unset=True)) - {"sources"}
    source_fields = (
        frozenset(payload.sources.model_dump(exclude_unset=True))
        if payload.sources is not None
        else frozenset()
    )
    try:
        updated = update_ops_podcast(
            db=db,
            podcast_id=podcast_id,
            payload=UpdateOpsPodcastInputData(
                provided_fields=provided,
                source_fields=source_fields,
                name=payload.name,
                slug=payload.slug,
                status=payload.status,
                description=payload.description,
                cover_url=payload.cover_url,
                discovery_source=payload.discovery_source,
                sources=(
                    OpsPodcastSourceInputData(**payload.sources.model_dump())
                    if payload.sources is not None
                    else None
                ),
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    if updated is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_PODCAST,
        resource_type="podcast",
        resource_id=updated.id,
        resource_identifier=updated.slug,
        summary=f"Updated podcast {updated.slug}",
        metadata_json={"fields": sorted(provided | source_fields)},
    )
    db.commit()
    return OpsPodcastRead.model_validate(updated, from_attributes=True)


def archive_ops_podcast_endpoint(
    podcast_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsPodcastRead:
    """Pause a managed podcast and record the action."""
    _require_ops_access(request=request, settings=settings)
    archived = archive_ops_podcast(db=db, podcast_id=podcast_id)
    if archived is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    record_audit_log(
        db=db,
        action=AuditAction.ARCHIVE_PODCAST,
        resource_type="podcast",
        resource_id=archived.id,
        resource_identifier=archived.slug,
        summary=f"Archived podcast {archived.slug}",
    )
    db.commit()
    return OpsPodcastRead.model_validate(archived, from_attributes=True)


def get_ops_podcast_endpoint(
    podcast_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsPodcastRead:
    """Return one managed podcast summary."""
    _require_ops_access(request=request, settings=settings)
    podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast_id)
    if podcast is None:
        raise HTTPException(status_code=404, detail="Podcast not found")
    db.commit()
    return OpsPodcastRead.model_validate(podcast, from_attributes=True)


def get_ops_pipelines(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OpsPipelineActivityRead:
    """Return recent ingestion run activity."""
    _require_ops_access(request=request, settings=settings)
    runs = list_recent_ingestion_runs(db=db, limit=limit)
    db.commit()
    return OpsPipelineActivityRead.model_validate(
        {"runs": runs},
        from_attributes=True,
    )


def list_ops_retention(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
) -> list[OpsTranscriptRetentionRead]:
    """List recent transcript assets for retention review."""
    _require_ops_access(request=request, settings=settings)
    listed = list_ops_transcript_retention(db=db, limit=limit)
    db.commit()
    return [
        OpsTranscriptRetentionRead.model_validate(item, from_attributes=True)
        for item in listed
    ]


def preview_ops_retention(
    transcript_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsRetentionPreviewRead:
    """Dry-run the retention policy for one transcript."""
    _require_ops_access(request=request, settings=settings)
    preview = preview_ops_transcript_retention(
        db=db,
        transcript_id=transcript_id,
        policy=TranscriptRetentionPolicy(),
    )
    if preview is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    db.commit()
    return _to_retention_preview_read(preview=preview)


def apply_ops_retention(
    transcript_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsRetentionPreviewRead:
    """Persist a retention evaluation and record the action."""
    _require_ops_access(request=request, settings=settings)
    applied = apply_ops_transcript_retention(
        db=db,
        transcript_id=transcript_id,
        policy=TranscriptRetentionPolicy(),
    )
    if applied is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    record_audit_log(
        db=db,
        action=AuditAction.EVALUATE_TRANSCRIPT_RETENTION,
        resource_type="transcript",
        resource_id=transcript_id,
        summary=f"Evaluated retention for transcript {transcript_id}",
        metadata_json={"tier": applied.transcript.tier},
    )
    db.commit()
    return _to_retention_preview_read(preview=applied)


def purge_ops_transcript_endpoint(
    transcript_id: int,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsTranscriptPurgeRead:
    """Purge an eligible transcript and record the action."""
    _require_ops_access(request=request, settings=settings)
    try:
        purged = purge_ops_transcript(
            db=db,
            transcript_id=transcript_id,
            artifact_store=build_transcript_artifact_store(settings=settings),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if purged is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    record_audit_log(
        db=db,
        action=AuditAction.PURGE_TRANSCRIPT,
        resource_type="transcript",
        resource_id=transcript_id,
        summary=f"Purged transcript {transcript_id}",
        metadata_json={"digest_id": purged.digest.id},
    )
    db.commit()
    return OpsTranscriptPurgeRead(
        transcript=OpsTranscriptRetentionRead.model_validate(
            purged.transcript,
            from_attributes=True,
        ),
        digest_id=purged.digest.id,
    )


def list_ops_takedown_requests(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    status_filter: Annotated[
        TakedownRequestStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[OpsTakedownRequestRead]:
    """List submitted takedown cases for privileged review."""
    _require_ops_access(request=request, settings=settings)
    listed = list_takedown_requests(db=db, status=status_filter, limit=limit)
    db.commit()
    return [
        OpsTakedownRequestRead.model_validate(item, from_attributes=True)
        for item in listed
    ]


def decide_ops_takedown_request(
    request_id: int,
    payload: OpsTakedownDecisionRequest,
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> OpsTakedownDecisionRead:
    """Decide a pending takedown; approvals execute suppression immediately."""
    _require_ops_access(request=request, settings=settings)
    try:
        decided = decide_takedown_request(
            db=db,
            request_id=request_id,
            payload=TakedownDecisionInputData(
                status=TakedownRequestStatus(payload.status),
                actor_name=payload.actor_name,
                note=payload.note,
            ),
        )
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    if decided is None:
        raise HTTPException(status_code=404, detail="Takedown request not found")
    execution = None
    if decided.status == TakedownRequestStatus.APPROVED.value:
        try:
            result = execute_approved_takedown_request(
                db=db,
                request=decided,
                artifact_store=build_transcript_artifact_store(settings=settings),
            )
        except ValueError as error:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(error)) from error
        execution = OpsTakedownExecutionRead(
            episode_ids=list(result.episode_ids),
            media_ids=list(result.media_ids),
            transcripts_suppressed=result.transcripts_suppressed,
            derivatives_suppressed=result.derivatives_suppressed,
            mentions_unpublished=result.mentions_unpublished,
            source_opt_outs_registered=result.source_opt_outs_registered,
        )
    record_audit_log(
        db=db,
        action=AuditAction.DECIDE_TAKEDOWN_REQUEST,
        resource_type="takedown_request",
        resource_id=decided.id,
        actor_name=payload.actor_name,
        summary=f"Takedown request {decided.id} {decided.status}",
        metadata_json=(execution.model_dump() if execution is not None else None),
    )
    db.commit()
    return OpsTakedownDecisionRead(
        request=OpsTakedownRequestRead.model_validate(
            decided,
            from_attributes=True,
        ),
        execution=execution,
    )


def get_ops_audit_log(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 25,
    resource_type: str | None = None,
) -> OpsAuditLogListRead:
    """List immutable audit records, newest first."""
    _require_ops_access(request=request, settings=settings)
    listed = list_audit_logs(
        db=db,
        page=page,
        per_page=per_page,
        resource_type=resource_type,
    )
    db.commit()
    return OpsAuditLogListRead(
        items=[
            OpsAuditLogEntryRead.model_validate(item, from_attributes=True)
            for item in listed.items
        ],
        total=listed.total,
        page=listed.page,
        per_page=listed.per_page,
    )


router.add_api_route("/metrics", get_ops_metrics, methods=["GET"])
router.add_api_route(
    "/alerts",
    get_ops_operational_alerts,
    methods=["GET"],
    response_model=OpsOperationalAlertListRead,
)
router.add_api_route(
    "/podcasts",
    list_ops_podcasts_endpoint,
    methods=["GET"],
    response_model=OpsPodcastListRead,
)
router.add_api_route(
    "/podcasts",
    create_ops_podcast_endpoint,
    methods=["POST"],
    response_model=OpsPodcastRead,
    status_code=status.HTTP_201_CREATED,
)
router.add_api_route(
    "/podcasts/{podcast_id}",
    get_ops_podcast_endpoint,
    methods=["GET"],
    response_model=OpsPodcastRead,
)
router.add_api_route(
    "/podcasts/{podcast_id}",
    update_ops_podcast_endpoint,
    methods=["PATCH"],
    response_model=OpsPodcastRead,
)
router.add_api_route(
    "/podcasts/{podcast_id}/archive",
    archive_ops_podcast_endpoint,
    methods=["POST"],
    response_model=OpsPodcastRead,
)
router.add_api_route(
    "/pipelines",
    get_ops_pipelines,
    methods=["GET"],
    response_model=OpsPipelineActivityRead,
)
router.add_api_route(
    "/retention",
    list_ops_retention,
    methods=["GET"],
    response_model=list[OpsTranscriptRetentionRead],
)
router.add_api_route(
    "/retention/{transcript_id}/preview",
    preview_ops_retention,
    methods=["GET"],
    response_model=OpsRetentionPreviewRead,
)
router.add_api_route(
    "/retention/{transcript_id}/apply",
    apply_ops_retention,
    methods=["POST"],
    response_model=OpsRetentionPreviewRead,
)
router.add_api_route(
    "/retention/{transcript_id}/purge",
    purge_ops_transcript_endpoint,
    methods=["POST"],
    response_model=OpsTranscriptPurgeRead,
)
router.add_api_route(
    "/takedown-requests",
    list_ops_takedown_requests,
    methods=["GET"],
    response_model=list[OpsTakedownRequestRead],
)
router.add_api_route(
    "/takedown-requests/{request_id}/decide",
    decide_ops_takedown_request,
    methods=["POST"],
    response_model=OpsTakedownDecisionRead,
)
router.add_api_route(
    "/audit-log",
    get_ops_audit_log,
    methods=["GET"],
    response_model=OpsAuditLogListRead,
)
