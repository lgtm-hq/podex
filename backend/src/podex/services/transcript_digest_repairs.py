"""Inventory and provenance-preserving repair of empty-source purge proofs.

Digests written before purge verified its source can carry the SHA-256 of
empty text even though nonempty text was acquired into an encrypted artifact.
These commands identify such rows and, only where the one artifact that
existed when the proof was written is still retained and verifiable, add a
separate repair digest. The original row is never rewritten, and deleted or
later input is never reconstructed, attributed or certified.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, auto

from sqlalchemy.orm import Session

from podex.models import TranscriptArtifact, TranscriptDigest
from podex.services.transcript_artifacts import TranscriptArtifactStore
from podex.services.transcript_retention_commands import (
    EMPTY_SOURCE_TEXT_HASH,
    resolve_verified_purge_source,
)

REPAIRS_DIGEST_KEY = "repairs_digest_id"


class EmptySourceDigestStatus(StrEnum):
    """What can be established about an empty-source digest."""

    REPAIRED = auto()
    RETAINED_ARTIFACT = auto()
    UNATTRIBUTABLE_ARTIFACT = auto()
    SOURCE_DELETED = auto()
    NO_RECORDED_SOURCE = auto()


@dataclass(frozen=True, slots=True)
class EmptySourceDigestData:
    """Classification of one digest whose proof hash is the empty-text hash.

    Attributes:
        digest_id: The empty-source digest.
        transcript_id: Transcript the digest belongs to.
        status: Repair state or what source evidence remains.
        recorded_artifact_hash: Hash recorded at acquisition by the newest
            artifact that predates the proof. It is metadata only; it is not a
            verification of deleted input.
        repair_digest_id: Repair record for this digest, when one exists.
    """

    digest_id: int
    transcript_id: int
    status: EmptySourceDigestStatus
    recorded_artifact_hash: str | None
    repair_digest_id: int | None


def inventory_empty_source_digests(*, db: Session) -> list[EmptySourceDigestData]:
    """Classify every digest whose recorded source hash is the empty-text hash.

    Args:
        db: Database session.

    Returns:
        One classification per empty-source digest, ordered by digest id.
    """
    digests = (
        db.query(TranscriptDigest)
        .filter(TranscriptDigest.source_text_hash == EMPTY_SOURCE_TEXT_HASH)
        .order_by(TranscriptDigest.id.asc())
        .all()
    )
    return [_classify(db=db, digest=digest) for digest in digests]


def repair_empty_source_digest(
    *,
    db: Session,
    digest_id: int,
    artifact_store: TranscriptArtifactStore | None,
    now: datetime,
) -> TranscriptDigest:
    """Record verified proof for an empty-source digest whose input is retained.

    Only an artifact attributable to the original proof qualifies: it must be
    the transcript's single retained artifact, the only artifact stored and
    recorded before the proof was written, and not a re-acquisition. Its
    content is verified against the hash recorded at acquisition, then a new
    digest referencing the original is added. ``generated_at`` and
    ``verified_at`` are the repair's verification time; ``purged_at`` and
    ``repairs_purged_at`` keep the original proof's purge time. No content,
    artifact or existing digest changes.

    Args:
        db: Database session.
        digest_id: Empty-source digest to repair.
        artifact_store: Configured encrypted artifact adapter.
        now: Repair timestamp.

    Returns:
        The new repair digest.

    Raises:
        ValueError: If the digest is unknown, not empty-source, already
            repaired, has no attributable retained artifact, or verification
            fails.
    """
    digest = db.query(TranscriptDigest).filter(TranscriptDigest.id == digest_id).first()
    if digest is None or digest.source_text_hash != EMPTY_SOURCE_TEXT_HASH:
        raise ValueError("Digest is not an empty-source purge proof")
    classification = _classify(db=db, digest=digest)
    artifact = _attributable_artifact(db=db, digest=digest)
    if (
        classification.status is not EmptySourceDigestStatus.RETAINED_ARTIFACT
        or artifact is None
    ):
        raise ValueError(
            f"Digest cannot be repaired: {classification.status.value}",
        )
    source = resolve_verified_purge_source(
        transcript=digest.transcript,
        artifacts=[artifact],
        artifact_store=artifact_store,
    )
    digest_key = f"transcript:{digest.transcript_id}:purge:{source.source_hash}"
    if (
        db.query(TranscriptDigest.id)
        .filter(TranscriptDigest.digest_key == digest_key)
        .first()
        is not None
    ):
        raise ValueError("A proof for the verified source already exists")
    repair = TranscriptDigest(
        transcript_id=digest.transcript_id,
        episode_id=digest.episode_id,
        digest_key=digest_key,
        source_text_hash=source.source_hash,
        provider=digest.provider,
        policy_version=digest.policy_version,
        summary_text=digest.summary_text,
        sampling_strata_json=digest.sampling_strata_json,
        extraction_versions_json=digest.extraction_versions_json,
        metadata_json={
            **(digest.metadata_json or {}),
            "source_representation": source.representation.value,
            "source_artifact_id": source.artifact_id,
            REPAIRS_DIGEST_KEY: digest.id,
            "repairs_purged_at": _as_utc(digest.purged_at).isoformat(),
            "verified_at": now.isoformat(),
        },
        generated_at=now,
        purged_at=digest.purged_at,
    )
    db.add(repair)
    db.flush()
    return repair


def _classify(*, db: Session, digest: TranscriptDigest) -> EmptySourceDigestData:
    """Classify one empty-source digest from repairs and artifact metadata."""
    repair_id = next(
        (
            other.id
            for other in db.query(TranscriptDigest)
            .filter(
                TranscriptDigest.transcript_id == digest.transcript_id,
                TranscriptDigest.id != digest.id,
            )
            .order_by(TranscriptDigest.id.asc())
            .all()
            if (other.metadata_json or {}).get(REPAIRS_DIGEST_KEY) == digest.id
        ),
        None,
    )
    artifacts = _artifacts_newest_first(db=db, transcript_id=digest.transcript_id)
    predating = [item for item in artifacts if _predates(artifact=item, digest=digest)]
    if repair_id is not None:
        status = EmptySourceDigestStatus.REPAIRED
    elif _attributable_artifact(db=db, digest=digest) is not None:
        status = EmptySourceDigestStatus.RETAINED_ARTIFACT
    elif any(artifact.purged_at is None for artifact in artifacts):
        status = EmptySourceDigestStatus.UNATTRIBUTABLE_ARTIFACT
    elif artifacts:
        status = EmptySourceDigestStatus.SOURCE_DELETED
    else:
        status = EmptySourceDigestStatus.NO_RECORDED_SOURCE
    return EmptySourceDigestData(
        digest_id=digest.id,
        transcript_id=digest.transcript_id,
        status=status,
        recorded_artifact_hash=predating[0].source_text_hash if predating else None,
        repair_digest_id=repair_id,
    )


def _attributable_artifact(
    *,
    db: Session,
    digest: TranscriptDigest,
) -> TranscriptArtifact | None:
    """Return the retained artifact the original proof must have covered.

    Exactly one artifact may predate the proof, it must still be retained,
    and no other artifact may be retained; anything else is ambiguous.
    """
    artifacts = _artifacts_newest_first(db=db, transcript_id=digest.transcript_id)
    predating = [item for item in artifacts if _predates(artifact=item, digest=digest)]
    retained = [item for item in artifacts if item.purged_at is None]
    if len(predating) == 1 and retained == predating:
        return predating[0]
    return None


def _artifacts_newest_first(
    *,
    db: Session,
    transcript_id: int,
) -> list[TranscriptArtifact]:
    """List every artifact ever stored for one transcript, newest first."""
    return list(
        db.query(TranscriptArtifact)
        .filter(TranscriptArtifact.transcript_id == transcript_id)
        .order_by(TranscriptArtifact.id.desc())
        .all()
    )


def _predates(*, artifact: TranscriptArtifact, digest: TranscriptDigest) -> bool:
    """Whether the artifact and its acquisition hash existed before the proof."""
    return (
        artifact.reacquired_from_digest_id is None
        and _as_utc(artifact.stored_at) < _as_utc(digest.generated_at)
        and _as_utc(artifact.created_at) <= _as_utc(digest.created_at)
    )


def _as_utc(value: datetime) -> datetime:
    """Treat naive database timestamps as UTC so comparisons are portable."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
