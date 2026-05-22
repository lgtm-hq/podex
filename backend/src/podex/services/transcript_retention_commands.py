"""Persistence commands for transcript retention policy decisions."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from podex.models import Transcript
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
    transcript.retention_exempt_sample = sample.retention_exempt
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
    now: datetime | None = None,
) -> bool:
    """Purge raw transcript fields when the latest evaluation allows it.

    Args:
        db: Database session.
        transcript: Transcript to purge.
        now: Deterministic purge timestamp.

    Returns:
        ``True`` when raw transcript fields were purged.
    """
    if transcript.purge_eligible_at is None or transcript.purged_at is not None:
        return False

    transcript.raw_text = None
    transcript.cleaned_text = None
    transcript.segments_json = None
    transcript.purged_at = now or datetime.now(UTC)
    transcript.retention_tier = "purged"
    db.flush()
    return True
