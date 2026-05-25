"""Takedown intake and privileged decision services."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    GraphTriple,
    Mention,
    MentionCandidate,
    SemanticChunk,
    TakedownRequest,
    TakedownRequesterType,
    TakedownRequestStatus,
    TakedownSubjectType,
    Transcript,
    TranscriptArtifact,
)
from podex.services.transcript_artifacts import TranscriptArtifactStore
from podex.services.transcript_retention import TranscriptRetentionPolicy
from podex.services.transcript_retention_commands import create_transcript_digest
from podex.services.transcript_retention_policies import (
    get_transcript_source_retention_policy,
    to_retention_policy,
    upsert_transcript_source_retention_policy,
)


@dataclass(frozen=True, slots=True)
class TakedownRequestInputData:
    """Validated input for one takedown submission."""

    subject_type: TakedownSubjectType
    subject_id: int
    requester_type: TakedownRequesterType
    requester_name: str
    requester_email: str
    basis: str
    requested_actions: list[str]


@dataclass(frozen=True, slots=True)
class TakedownDecisionInputData:
    """Operator decision for one submitted request."""

    status: TakedownRequestStatus
    actor_name: str | None
    note: str


@dataclass(frozen=True, slots=True)
class TakedownExecutionResultData:
    """Counts and public projection identifiers affected by suppression."""

    episode_ids: tuple[int, ...]
    media_ids: tuple[int, ...]
    transcripts_suppressed: int
    derivatives_suppressed: int
    mentions_unpublished: int
    source_opt_outs_registered: int


def create_takedown_request(
    *,
    db: Session,
    payload: TakedownRequestInputData,
) -> TakedownRequest:
    """Persist a newly submitted takedown case in pending review."""
    request = TakedownRequest(
        subject_type=payload.subject_type.value,
        subject_id=payload.subject_id,
        requester_type=payload.requester_type.value,
        requester_name=payload.requester_name,
        requester_email=payload.requester_email,
        basis=payload.basis,
        requested_actions_json=payload.requested_actions,
        status=TakedownRequestStatus.PENDING.value,
    )
    db.add(request)
    db.flush()
    return request


def list_takedown_requests(
    *,
    db: Session,
    status: TakedownRequestStatus | None = None,
    limit: int = 50,
) -> list[TakedownRequest]:
    """List submitted takedown cases for privileged operator review."""
    query = db.query(TakedownRequest)
    if status is not None:
        query = query.filter(TakedownRequest.status == status.value)
    return (
        query.order_by(TakedownRequest.created_at.desc(), TakedownRequest.id.desc())
        .limit(limit)
        .all()
    )


def decide_takedown_request(
    *,
    db: Session,
    request_id: int,
    payload: TakedownDecisionInputData,
    now: datetime | None = None,
) -> TakedownRequest | None:
    """Record approval or rejection for one pending takedown request."""
    request = db.query(TakedownRequest).filter(TakedownRequest.id == request_id).first()
    if request is None:
        return None
    if request.status != TakedownRequestStatus.PENDING.value:
        raise ValueError("Takedown request has already been decided")
    request.status = payload.status.value
    request.decided_by = payload.actor_name
    request.decision_note = payload.note
    request.decided_at = now or datetime.now(UTC)
    db.flush()
    return request


def execute_approved_takedown_request(
    *,
    db: Session,
    request: TakedownRequest,
    artifact_store: TranscriptArtifactStore | None,
    now: datetime | None = None,
) -> TakedownExecutionResultData:
    """Execute data removal and opt-out actions for an approved request."""
    if request.status != TakedownRequestStatus.APPROVED.value:
        raise ValueError("Only approved takedown requests can be executed")

    effective_now = now or datetime.now(UTC)
    actions = set(request.requested_actions_json)
    episode_ids = _episode_ids_for_request(db=db, request=request)
    transcripts = (
        db.query(Transcript).filter(Transcript.episode_id.in_(episode_ids)).all()
        if episode_ids
        else []
    )
    transcripts_suppressed = 0
    derivatives_suppressed = 0
    mentions_unpublished = 0
    source_opt_outs_registered = 0
    media_ids: tuple[int, ...] = ()

    if "suppress_raw_transcript" in actions:
        transcripts_suppressed = _suppress_raw_transcripts(
            db=db,
            transcripts=transcripts,
            artifact_store=artifact_store,
            suppressed_at=effective_now,
        )
    if "suppress_derivatives" in actions:
        derivatives_suppressed = _suppress_derivatives(
            db=db,
            episode_ids=episode_ids,
        )
    if "unpublish_mentions" in actions:
        mentions_unpublished, media_ids = _unpublish_mentions(
            db=db,
            request=request,
            episode_ids=episode_ids,
        )
    if "register_source_opt_out" in actions:
        if request.requester_type != TakedownRequesterType.CREATOR.value:
            raise ValueError("Source opt-out registration requires a creator request")
        source_opt_outs_registered = _register_source_opt_outs(
            db=db,
            transcripts=transcripts,
        )

    result = TakedownExecutionResultData(
        episode_ids=episode_ids,
        media_ids=media_ids,
        transcripts_suppressed=transcripts_suppressed,
        derivatives_suppressed=derivatives_suppressed,
        mentions_unpublished=mentions_unpublished,
        source_opt_outs_registered=source_opt_outs_registered,
    )
    request.metadata_json = {
        **(request.metadata_json or {}),
        "executed_at": effective_now.isoformat(),
        "transcripts_suppressed": result.transcripts_suppressed,
        "derivatives_suppressed": result.derivatives_suppressed,
        "mentions_unpublished": result.mentions_unpublished,
        "source_opt_outs_registered": result.source_opt_outs_registered,
    }
    db.flush()
    return result


def _episode_ids_for_request(
    *,
    db: Session,
    request: TakedownRequest,
) -> tuple[int, ...]:
    """Resolve the episodes covered by a catalog subject."""
    if request.subject_type == TakedownSubjectType.PODCAST.value:
        rows = (
            db.query(Episode.id).filter(Episode.podcast_id == request.subject_id).all()
        )
        return tuple(row[0] for row in rows)
    if request.subject_type == TakedownSubjectType.EPISODE.value:
        return (request.subject_id,)
    mention = db.query(Mention).filter(Mention.id == request.subject_id).first()
    return (mention.episode_id,) if mention is not None else ()


def _suppress_raw_transcripts(
    *,
    db: Session,
    transcripts: list[Transcript],
    artifact_store: TranscriptArtifactStore | None,
    suppressed_at: datetime,
) -> int:
    """Immediately delete retained raw payloads covered by an approval."""
    suppressed = 0
    for transcript in transcripts:
        artifacts = (
            db.query(TranscriptArtifact)
            .filter(
                TranscriptArtifact.transcript_id == transcript.id,
                TranscriptArtifact.purged_at.is_(None),
            )
            .all()
        )
        if (
            not artifacts
            and transcript.raw_text is None
            and transcript.segments_json is None
        ):
            continue
        if artifacts and artifact_store is None:
            raise ValueError("Encrypted transcript artifact storage is not configured")
        create_transcript_digest(
            db=db,
            transcript=transcript,
            purged_at=suppressed_at,
        )
        for artifact in artifacts:
            assert artifact_store is not None
            artifact_store.delete(storage_key=artifact.storage_key)
            artifact.purged_at = suppressed_at
        transcript.raw_text = None
        transcript.segments_json = None
        transcript.purged_at = suppressed_at
        transcript.retention_tier = "purged"
        suppressed += 1
    return suppressed


def _suppress_derivatives(*, db: Session, episode_ids: tuple[int, ...]) -> int:
    """Delete generated summaries and semantic retrieval content."""
    if not episode_ids:
        return 0
    run_count = (
        db.query(DerivativeGenerationRun)
        .filter(DerivativeGenerationRun.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    chunk_count = (
        db.query(SemanticChunk)
        .filter(SemanticChunk.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    summary_count = (
        db.query(EpisodeSummary)
        .filter(EpisodeSummary.episode_id.in_(episode_ids))
        .delete(synchronize_session=False)
    )
    db.query(Transcript).filter(Transcript.episode_id.in_(episode_ids)).update(
        {
            Transcript.cleaned_text: None,
            Transcript.cleaned_at: None,
            Transcript.digest_text: None,
            Transcript.digest_created_at: None,
        },
        synchronize_session=False,
    )
    return run_count + chunk_count + summary_count


def _unpublish_mentions(
    *,
    db: Session,
    request: TakedownRequest,
    episode_ids: tuple[int, ...],
) -> tuple[int, tuple[int, ...]]:
    """Remove published mentions and detach retained provenance references."""
    query = db.query(Mention)
    if request.subject_type == TakedownSubjectType.MENTION.value:
        query = query.filter(Mention.id == request.subject_id)
    else:
        query = query.filter(Mention.episode_id.in_(episode_ids))
    mentions = query.all()
    if not mentions:
        return 0, ()
    mention_ids = [mention.id for mention in mentions]
    media_ids = tuple(sorted({mention.media_id for mention in mentions}))
    db.query(MentionCandidate).filter(
        MentionCandidate.mention_id.in_(mention_ids)
    ).update({MentionCandidate.mention_id: None}, synchronize_session=False)
    db.query(GraphTriple).filter(
        GraphTriple.provenance_mention_id.in_(mention_ids)
    ).update({GraphTriple.provenance_mention_id: None}, synchronize_session=False)
    for mention in mentions:
        db.delete(mention)
    db.flush()
    return len(mentions), media_ids


def _register_source_opt_outs(*, db: Session, transcripts: list[Transcript]) -> int:
    """Persist future-acquisition raw retention opt-outs for known sources."""
    known_sources: set[tuple[int, str]] = set()
    for transcript in transcripts:
        source_key = (transcript.episode.podcast_id, transcript.provider)
        if source_key in known_sources:
            continue
        stored_policy = get_transcript_source_retention_policy(
            db=db,
            transcript=transcript,
        )
        upsert_transcript_source_retention_policy(
            db=db,
            transcript=transcript,
            policy=(
                to_retention_policy(record=stored_policy)
                if stored_policy is not None
                else TranscriptRetentionPolicy()
            ),
            source_retention_opt_out=True,
        )
        known_sources.add(source_key)
    for transcript in transcripts:
        transcript.source_retention_opt_out = True
    return len(known_sources)
