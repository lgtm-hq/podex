"""Persistence commands for transcript retention policy decisions."""

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeGenerationRun,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.transcript_artifacts import TranscriptArtifactStore
from podex.services.transcript_retention import (
    TranscriptRetentionDecision,
    TranscriptRetentionPolicy,
    TranscriptRetentionSampleKey,
    TranscriptRetentionService,
    TranscriptRetentionState,
)


@dataclass(frozen=True, slots=True)
class TranscriptRetentionEvaluationData:
    """Persisted transcript retention evaluation summary."""

    transcript_id: int
    decision: TranscriptRetentionDecision
    retention_exempt_sample: bool


def create_transcript_digest(
    *,
    db: Session,
    transcript: Transcript,
    purged_at: datetime,
) -> TranscriptDigest:
    """Persist proof of raw-transcript processing before payload deletion.

    Args:
        db: Database session.
        transcript: Transcript about to be purged.
        purged_at: Timestamp of the purge operation.

    Returns:
        Replay-safe durable digest record.
    """
    source_text = transcript.cleaned_text or transcript.raw_text or ""
    source_hash = sha256(source_text.encode("utf-8")).hexdigest()
    digest_key = f"transcript:{transcript.id}:purge:{source_hash}"
    existing = (
        db.query(TranscriptDigest)
        .filter(TranscriptDigest.digest_key == digest_key)
        .first()
    )
    if existing is not None:
        return existing

    extraction_versions = [
        version
        for (version,) in db.query(DerivativeGenerationRun.pipeline_version)
        .filter(DerivativeGenerationRun.transcript_id == transcript.id)
        .distinct()
        .order_by(DerivativeGenerationRun.pipeline_version.asc())
        .all()
    ]
    summary_text = transcript.digest_text or _compact_digest_text(source_text)
    digest = TranscriptDigest(
        transcript_id=transcript.id,
        episode_id=transcript.episode_id,
        digest_key=digest_key,
        source_text_hash=source_hash,
        provider=transcript.provider,
        policy_version=transcript.retention_policy_version,
        summary_text=summary_text,
        sampling_strata_json=transcript.retention_sample_strata_json,
        extraction_versions_json=extraction_versions,
        metadata_json={
            "retention_tier": transcript.retention_tier,
            "retention_exempt_sample": transcript.retention_exempt_sample,
        },
        generated_at=purged_at,
        purged_at=purged_at,
    )
    db.add(digest)
    db.flush()
    return digest


def evaluate_transcript_retention(
    *,
    db: Session,
    transcript: Transcript,
    extraction_confidence: float | None,
    source_retention_opt_out: bool = False,
    policy: TranscriptRetentionPolicy | None = None,
    now: datetime | None = None,
) -> TranscriptRetentionEvaluationData:
    """Evaluate and persist retention state for a transcript.

    Args:
        db: Database session.
        transcript: Transcript to evaluate.
        extraction_confidence: Latest extraction confidence for purge gating.
        source_retention_opt_out: Whether the source suppresses raw retention.
        policy: Optional policy override.
        now: Deterministic evaluation timestamp.

    Returns:
        Persisted retention evaluation summary.
    """
    effective_now = now or datetime.now(UTC)
    service = TranscriptRetentionService(
        policy=policy or TranscriptRetentionPolicy(),
    )
    acquired_at = transcript.fetched_at or transcript.created_at
    sample = service.choose_sample(
        sample_key=TranscriptRetentionSampleKey(
            transcript_key=f"transcript:{transcript.id}",
            source_key=transcript.provider,
            topic_key=None,
        ),
        acquired_at=acquired_at,
        now=effective_now,
        extraction_confidence=extraction_confidence,
    )
    decision = service.evaluate(
        state=TranscriptRetentionState(
            acquired_at=acquired_at,
            extraction_confidence=extraction_confidence,
            digest_created_at=transcript.digest_created_at,
            purged_at=transcript.purged_at,
            retention_exempt_sample=sample.retention_exempt
            or transcript.retention_exempt_sample,
            source_retention_opt_out=source_retention_opt_out,
        ),
        now=effective_now,
    )

    transcript.retention_tier = decision.tier.value
    transcript.retention_policy_version = service.policy.sample_version
    transcript.retention_evaluated_at = effective_now
    transcript.retention_exempt_sample = (
        sample.retention_exempt or transcript.retention_exempt_sample
    )
    transcript.source_retention_opt_out = source_retention_opt_out
    transcript.retention_blockers_json = [
        blocker.value for blocker in decision.purge_blockers
    ]
    transcript.purge_eligible_at = effective_now if decision.purge_eligible else None
    db.flush()

    return TranscriptRetentionEvaluationData(
        transcript_id=transcript.id,
        decision=decision,
        retention_exempt_sample=sample.retention_exempt,
    )


def purge_transcript_if_eligible(
    *,
    db: Session,
    transcript: Transcript,
    artifact_store: TranscriptArtifactStore | None = None,
    now: datetime | None = None,
) -> bool:
    """Purge raw transcript fields when the latest evaluation allows it.

    Args:
        db: Database session.
        transcript: Transcript to purge.
        artifact_store: Private object adapter used to delete raw artifacts.
        now: Deterministic purge timestamp.

    Returns:
        ``True`` when raw transcript fields were purged.
    """
    if transcript.purge_eligible_at is None or transcript.purged_at is not None:
        return False

    purged_at = now or datetime.now(UTC)
    active_artifacts = (
        db.query(TranscriptArtifact)
        .filter(
            TranscriptArtifact.transcript_id == transcript.id,
            TranscriptArtifact.purged_at.is_(None),
        )
        .all()
    )
    if active_artifacts and artifact_store is None:
        raise ValueError("Encrypted transcript artifact storage is not configured")
    create_transcript_digest(db=db, transcript=transcript, purged_at=purged_at)
    for artifact in active_artifacts:
        assert artifact_store is not None
        artifact_store.delete(storage_key=artifact.storage_key)
        artifact.purged_at = purged_at
    transcript.raw_text = None
    transcript.cleaned_text = None
    transcript.segments_json = None
    transcript.purged_at = purged_at
    transcript.retention_tier = "purged"
    db.flush()
    return True


def _compact_digest_text(text: str, max_length: int = 1000) -> str:
    """Build a compact, non-empty digest summary from raw text."""
    compacted = " ".join(text.split())
    if not compacted:
        return "Raw transcript was purged after retention evaluation."
    if len(compacted) <= max_length:
        return compacted
    return f"{compacted[: max_length - 3].rstrip()}..."
