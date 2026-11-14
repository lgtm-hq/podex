"""Persistence commands for transcript retention policy decisions."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto
from hashlib import sha256

from sqlalchemy.orm import Session

from podex.config import RetentionSettings, get_settings
from podex.models import (
    DerivativeGenerationRun,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.transcript_artifacts import (
    TranscriptArtifactStore,
    list_retained_artifacts,
    verify_artifact_source_hash,
)
from podex.services.transcript_retention import (
    TranscriptRetentionDecision,
    TranscriptRetentionPolicy,
    TranscriptRetentionSampleKey,
    TranscriptRetentionService,
    TranscriptRetentionState,
)

PURGE_DISABLED_REASON = "Transcript purge is disabled by PODEX_RETENTION__PURGE_ENABLED"
NO_PURGE_SOURCE_REASON = "Transcript has no retained source text to prove before purge"
EMPTY_SOURCE_TEXT_HASH = sha256(b"").hexdigest()
# Retention confidence counts only when it comes from a completed extraction
# generation of the evaluated transcript (ADR 0006). Mention candidates record
# an episode but no transcript or extraction generation, so no transcript has
# such a confidence yet. Scheduled sweeps and operator preview/apply all use
# this value, so they reach the same decision and keep the missing-confidence
# purge blocker in force.
UNKNOWN_RETENTION_CONFIDENCE: float | None = None


class TranscriptSourceRepresentation(StrEnum):
    """Which retained representation a purge proof hash was computed from."""

    ENCRYPTED_ARTIFACT_RAW_TEXT = auto()
    LEGACY_CLEANED_TEXT = auto()
    LEGACY_RAW_TEXT = auto()
    ABSENT = auto()


@dataclass(frozen=True, slots=True)
class TranscriptProofSourceData:
    """Resolved identity of the source text a digest proves.

    Attributes:
        source_hash: SHA-256 of the representation's exact UTF-8 text.
        representation: Which retained representation was hashed.
        artifact_id: Verified artifact, for encrypted artifact sources.
        legacy_text: Column text eligible for the digest summary fallback.
            Always empty for artifacts so decrypted text is never persisted.
    """

    source_hash: str
    representation: TranscriptSourceRepresentation
    artifact_id: int | None = None
    legacy_text: str = ""


def require_transcript_purge_enabled(
    retention: RetentionSettings | None = None,
) -> None:
    """Refuse transcript deletion unless the environment explicitly opts in."""
    effective_retention = (
        retention if retention is not None else get_settings().retention
    )
    if not effective_retention.purge_enabled:
        raise ValueError(PURGE_DISABLED_REASON)


@dataclass(frozen=True, slots=True)
class TranscriptRetentionEvaluationData:
    """Persisted transcript retention evaluation summary."""

    transcript_id: int
    decision: TranscriptRetentionDecision
    retention_exempt_sample: bool


def resolve_column_proof_source(transcript: Transcript) -> TranscriptProofSourceData:
    """Describe the legacy plaintext columns without verifying any artifact.

    Cleaned text takes precedence over raw text, preserving the digest identity
    of transcripts whose proof was recorded before representations existed.
    Empty columns resolve to ``ABSENT`` with the empty-text hash, which names
    the absence instead of claiming it as the acquired source.

    Args:
        transcript: Transcript whose plaintext columns are described.

    Returns:
        Column-derived proof source.
    """
    if transcript.cleaned_text:
        text = transcript.cleaned_text
        representation = TranscriptSourceRepresentation.LEGACY_CLEANED_TEXT
    elif transcript.raw_text:
        text = transcript.raw_text
        representation = TranscriptSourceRepresentation.LEGACY_RAW_TEXT
    else:
        return TranscriptProofSourceData(
            source_hash=EMPTY_SOURCE_TEXT_HASH,
            representation=TranscriptSourceRepresentation.ABSENT,
        )
    return TranscriptProofSourceData(
        source_hash=sha256(text.encode("utf-8")).hexdigest(),
        representation=representation,
        legacy_text=text,
    )


def resolve_verified_purge_source(
    *,
    transcript: Transcript,
    artifacts: list[TranscriptArtifact],
    artifact_store: TranscriptArtifactStore | None,
) -> TranscriptProofSourceData:
    """Resolve and verify the retained source a purge will delete.

    Every given artifact is read and checked against its acquisition hash;
    the newest one identifies the proof. Without artifacts the legacy columns
    are used. This performs reads only and writes nothing.

    Args:
        transcript: Transcript about to be purged.
        artifacts: Exactly the unpurged artifacts the caller will delete.
        artifact_store: Configured encrypted artifact adapter.

    Returns:
        Verified proof source.

    Raises:
        ValueError: If storage is unconfigured, any artifact fails
            verification, or no source text is retained.
    """
    if not artifacts:
        source = resolve_column_proof_source(transcript)
        if source.representation is TranscriptSourceRepresentation.ABSENT:
            raise ValueError(NO_PURGE_SOURCE_REASON)
        return source
    if artifact_store is None:
        raise ValueError("Encrypted transcript artifact storage is not configured")
    hashes = {
        artifact.id: verify_artifact_source_hash(
            artifact=artifact,
            artifact_store=artifact_store,
        )
        for artifact in artifacts
    }
    selected_id = max(hashes)
    return TranscriptProofSourceData(
        source_hash=hashes[selected_id],
        representation=TranscriptSourceRepresentation.ENCRYPTED_ARTIFACT_RAW_TEXT,
        artifact_id=selected_id,
    )


def create_transcript_digest(
    *,
    db: Session,
    transcript: Transcript,
    source: TranscriptProofSourceData,
    purged_at: datetime,
) -> TranscriptDigest:
    """Persist proof of raw-transcript processing before payload deletion.

    Args:
        db: Database session.
        transcript: Transcript about to be purged.
        source: Resolved identity of the source text being proven.
        purged_at: Timestamp of the purge operation.

    Returns:
        Replay-safe durable digest record.
    """
    digest_key = f"transcript:{transcript.id}:purge:{source.source_hash}"
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
    summary_text = transcript.digest_text or _compact_digest_text(source.legacy_text)
    metadata: dict[str, object] = {
        "retention_tier": transcript.retention_tier,
        "retention_exempt_sample": transcript.retention_exempt_sample,
        "source_representation": source.representation.value,
    }
    if source.artifact_id is not None:
        metadata["source_artifact_id"] = source.artifact_id
    digest = TranscriptDigest(
        transcript_id=transcript.id,
        episode_id=transcript.episode_id,
        digest_key=digest_key,
        source_text_hash=source.source_hash,
        provider=transcript.provider,
        policy_version=transcript.retention_policy_version,
        summary_text=summary_text,
        sampling_strata_json=transcript.retention_sample_strata_json,
        extraction_versions_json=extraction_versions,
        metadata_json=metadata,
        generated_at=purged_at,
        purged_at=purged_at,
    )
    db.add(digest)
    db.flush()
    return digest


# Keep the existing named command inputs compatible with callers.
# pylint: disable-next=too-many-arguments
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
    retention: RetentionSettings | None = None,
) -> bool:
    """Purge raw transcript fields when the latest evaluation allows it.

    Args:
        db: Database session.
        transcript: Transcript to purge.
        artifact_store: Private object adapter used to delete raw artifacts.
        now: Deterministic purge timestamp.
        retention: Environment purge authorization; defaults to runtime settings.

    Returns:
        ``True`` when raw transcript fields were purged.
    """
    digest = purge_transcript_with_proof(
        db=db,
        transcript=transcript,
        artifact_store=artifact_store,
        now=now,
        retention=retention,
    )
    return digest is not None


def purge_transcript_with_proof(
    *,
    db: Session,
    transcript: Transcript,
    artifact_store: TranscriptArtifactStore | None = None,
    now: datetime | None = None,
    retention: RetentionSettings | None = None,
) -> TranscriptDigest | None:
    """Verify the retained source, persist its proof, then delete the payload.

    Nothing is written or deleted until every retained artifact has been read
    and matched to its acquisition hash.

    Args:
        db: Database session.
        transcript: Transcript to purge.
        artifact_store: Private object adapter used to read and delete artifacts.
        now: Deterministic purge timestamp.
        retention: Environment purge authorization; defaults to runtime settings.

    Returns:
        The proof digest for this purge, or ``None`` when not eligible.

    Raises:
        ValueError: If purge is disabled or the retained source cannot be
            verified; no content or proof is changed in that case.
    """
    require_transcript_purge_enabled(retention)
    if transcript.purge_eligible_at is None or transcript.purged_at is not None:
        return None

    purged_at = now or datetime.now(UTC)
    active_artifacts = list_retained_artifacts(db=db, transcript_id=transcript.id)
    source = resolve_verified_purge_source(
        transcript=transcript,
        artifacts=active_artifacts,
        artifact_store=artifact_store,
    )
    digest = create_transcript_digest(
        db=db,
        transcript=transcript,
        source=source,
        purged_at=purged_at,
    )
    for artifact in active_artifacts:
        if artifact_store is None:  # pragma: no cover - guarded above
            raise AssertionError
        artifact_store.delete(storage_key=artifact.storage_key)
        artifact.purged_at = purged_at
    transcript.raw_text = None
    transcript.cleaned_text = None
    transcript.segments_json = None
    transcript.purged_at = purged_at
    transcript.retention_tier = "purged"
    db.flush()
    return digest


def _compact_digest_text(text: str, max_length: int = 1000) -> str:
    """Build a compact, non-empty digest summary from raw text."""
    compacted = " ".join(text.split())
    if not compacted:
        return "Raw transcript was purged after retention evaluation."
    if len(compacted) <= max_length:
        return compacted
    return f"{compacted[: max_length - 3].rstrip()}..."
