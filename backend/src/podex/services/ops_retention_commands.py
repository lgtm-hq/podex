"""Operator commands and read models for transcript lifecycle management."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import (
    MentionCandidate,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.derivative_coverage import evaluate_episode_derivative_coverage
from podex.services.transcript_artifacts import (
    TranscriptAcquisitionClient,
    TranscriptArtifactStore,
    TranscriptReacquisitionData,
    reacquire_purged_transcript,
)
from podex.services.transcript_retention import (
    TranscriptRetentionDecision,
    TranscriptRetentionPolicy,
    TranscriptRetentionService,
    TranscriptRetentionState,
)
from podex.services.transcript_retention_commands import (
    evaluate_transcript_retention,
    purge_transcript_if_eligible,
)


@dataclass(frozen=True, slots=True)
class OpsTranscriptRetentionData:
    """Operator-facing state for one raw transcript asset."""

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


@dataclass(frozen=True, slots=True)
class OpsTranscriptRetentionPreviewData:
    """Dry-run lifecycle result plus derivative safety gate."""

    transcript: OpsTranscriptRetentionData
    decision: TranscriptRetentionDecision
    extraction_confidence: float | None
    derivative_coverage_ready: bool
    missing_query_classes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpsTranscriptPurgeResultData:
    """Persisted transcript and proof digest after an operator purge."""

    transcript: OpsTranscriptRetentionData
    digest: TranscriptDigest


@dataclass(frozen=True, slots=True)
class OpsTranscriptReacquisitionResultData:
    """Fresh transcript asset created for reprocessing after purge."""

    transcript: OpsTranscriptRetentionData
    artifact: TranscriptArtifact
    prior_digest: TranscriptDigest


def list_ops_transcript_retention(
    *,
    db: Session,
    limit: int = 40,
) -> list[OpsTranscriptRetentionData]:
    """List recent transcript assets for operator retention review."""
    transcripts = db.query(Transcript).order_by(Transcript.id.desc()).limit(limit).all()
    return [
        _to_ops_transcript_retention(db=db, transcript=item) for item in transcripts
    ]


def preview_ops_transcript_retention(
    *,
    db: Session,
    transcript_id: int,
    policy: TranscriptRetentionPolicy,
    source_retention_opt_out: bool | None = None,
    now: datetime | None = None,
) -> OpsTranscriptRetentionPreviewData | None:
    """Dry-run a lifecycle policy without changing transcript state."""
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript is None:
        return None
    extraction_confidence = _latest_confidence(db=db, transcript=transcript)
    service = TranscriptRetentionService(policy=policy)
    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=transcript.fetched_at or transcript.created_at,
            extraction_confidence=extraction_confidence,
            digest_created_at=transcript.digest_created_at,
            purged_at=transcript.purged_at,
            retention_exempt_sample=transcript.retention_exempt_sample,
            source_retention_opt_out=(
                source_retention_opt_out
                if source_retention_opt_out is not None
                else transcript.source_retention_opt_out
            ),
        ),
        now=now or datetime.now(UTC),
    )
    coverage = evaluate_episode_derivative_coverage(db=db, episode=transcript.episode)
    return OpsTranscriptRetentionPreviewData(
        transcript=_to_ops_transcript_retention(db=db, transcript=transcript),
        decision=decision,
        extraction_confidence=extraction_confidence,
        derivative_coverage_ready=coverage.purge_safe,
        missing_query_classes=tuple(
            item.value for item in coverage.missing_query_classes
        ),
    )


def apply_ops_transcript_retention(
    *,
    db: Session,
    transcript_id: int,
    policy: TranscriptRetentionPolicy,
    source_retention_opt_out: bool | None = None,
    now: datetime | None = None,
) -> OpsTranscriptRetentionPreviewData | None:
    """Persist a policy evaluation and return the current purge gate state."""
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript is None:
        return None
    evaluate_transcript_retention(
        db=db,
        transcript=transcript,
        extraction_confidence=_latest_confidence(db=db, transcript=transcript),
        source_retention_opt_out=(
            source_retention_opt_out
            if source_retention_opt_out is not None
            else transcript.source_retention_opt_out
        ),
        policy=policy,
        now=now,
    )
    return preview_ops_transcript_retention(
        db=db,
        transcript_id=transcript_id,
        policy=policy,
        source_retention_opt_out=source_retention_opt_out,
        now=now,
    )


def purge_ops_transcript(
    *,
    db: Session,
    transcript_id: int,
    artifact_store: TranscriptArtifactStore | None = None,
    now: datetime | None = None,
) -> OpsTranscriptPurgeResultData | None:
    """Purge an eligible transcript after checking public derivative coverage."""
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript is None:
        return None
    coverage = evaluate_episode_derivative_coverage(db=db, episode=transcript.episode)
    if not coverage.purge_safe:
        raise ValueError("Derivative coverage is incomplete for transcript purge")
    if not purge_transcript_if_eligible(
        db=db,
        transcript=transcript,
        artifact_store=artifact_store,
        now=now,
    ):
        raise ValueError("Transcript is not eligible for purge")
    digest = (
        db.query(TranscriptDigest)
        .filter(TranscriptDigest.transcript_id == transcript.id)
        .order_by(TranscriptDigest.id.desc())
        .one()
    )
    return OpsTranscriptPurgeResultData(
        transcript=_to_ops_transcript_retention(db=db, transcript=transcript),
        digest=digest,
    )


def reacquire_ops_transcript(
    *,
    db: Session,
    transcript_id: int,
    acquirer: TranscriptAcquisitionClient,
    artifact_store: TranscriptArtifactStore | None,
) -> OpsTranscriptReacquisitionResultData | None:
    """Re-acquire a purged transcript into a fresh hot raw asset."""
    transcript = db.query(Transcript).filter(Transcript.id == transcript_id).first()
    if transcript is None:
        return None
    result: TranscriptReacquisitionData = reacquire_purged_transcript(
        db=db,
        transcript=transcript,
        acquirer=acquirer,
        artifact_store=artifact_store,
    )
    return OpsTranscriptReacquisitionResultData(
        transcript=_to_ops_transcript_retention(db=db, transcript=result.transcript),
        artifact=result.artifact,
        prior_digest=result.prior_digest,
    )


def _latest_confidence(*, db: Session, transcript: Transcript) -> float | None:
    """Get the highest candidate confidence for the transcript episode."""
    row = (
        db.query(MentionCandidate.confidence)
        .filter(MentionCandidate.episode_id == transcript.episode_id)
        .order_by(MentionCandidate.confidence.desc())
        .first()
    )
    return row[0] if row is not None else None


def _to_ops_transcript_retention(
    *,
    db: Session,
    transcript: Transcript,
) -> OpsTranscriptRetentionData:
    """Convert a transcript model into its ops lifecycle read model."""
    digest = (
        db.query(TranscriptDigest)
        .filter(TranscriptDigest.transcript_id == transcript.id)
        .order_by(TranscriptDigest.id.desc())
        .first()
    )
    active_artifact = (
        db.query(TranscriptArtifact)
        .filter(
            TranscriptArtifact.transcript_id == transcript.id,
            TranscriptArtifact.purged_at.is_(None),
        )
        .first()
    )
    return OpsTranscriptRetentionData(
        id=transcript.id,
        episode_id=transcript.episode_id,
        episode_title=transcript.episode.title,
        podcast_name=transcript.episode.podcast.name,
        provider=transcript.provider,
        fetched_at=transcript.fetched_at,
        tier=transcript.retention_tier,
        policy_version=transcript.retention_policy_version,
        retention_exempt_sample=transcript.retention_exempt_sample,
        source_retention_opt_out=transcript.source_retention_opt_out,
        purge_eligible_at=transcript.purge_eligible_at,
        purged_at=transcript.purged_at,
        has_raw_payload=(
            transcript.raw_text is not None
            or transcript.cleaned_text is not None
            or active_artifact is not None
        ),
        has_stored_artifact=active_artifact is not None,
        digest_id=digest.id if digest is not None else None,
    )
