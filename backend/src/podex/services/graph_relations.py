"""Media relation and external-reference persistence services.

Graph-triple persistence lands with the derivatives theme; this module
carries the merge-critical upserts only.
"""

from __future__ import annotations

from hashlib import sha256

from sqlalchemy.orm import Session

from podex.models import (
    Media,
    MediaExternalRef,
    MediaExternalRefSource,
    MediaRelation,
    MediaRelationType,
)


def upsert_media_external_ref(
    *,
    db: Session,
    media: Media,
    source: MediaExternalRefSource,
    external_id: str,
    url: str | None = None,
    label: str | None = None,
    description: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> MediaExternalRef:
    """Create or update an external reference for a media record.

    Args:
        db: Database session.
        media: Media record to attach the reference to.
        source: External source namespace.
        external_id: Source-specific identifier.
        url: Optional canonical URL.
        label: Optional display label.
        description: Optional reference description.
        metadata_json: Optional source metadata.

    Returns:
        Existing or newly created media external reference.
    """
    normalized_external_id = _normalize_required(
        value=external_id,
        field_name="external_id",
    )
    external_ref = (
        db.query(MediaExternalRef)
        .filter(MediaExternalRef.media_id == media.id)
        .filter(MediaExternalRef.source == source.value)
        .filter(MediaExternalRef.external_id == normalized_external_id)
        .first()
    )
    if external_ref is None:
        external_ref = MediaExternalRef(
            media_id=media.id,
            source=source.value,
            external_id=normalized_external_id,
        )
        db.add(external_ref)

    external_ref.url = _normalize_optional(url)
    external_ref.label = _normalize_optional(label)
    external_ref.description = _normalize_optional(description)
    external_ref.metadata_json = metadata_json
    db.flush()
    return external_ref


def upsert_media_relation(
    *,
    db: Session,
    subject_media_id: int,
    object_media_id: int,
    relation_type: MediaRelationType,
    source: str,
    provenance_episode_id: int | None = None,
    confidence: float = 1.0,
    evidence_text: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> MediaRelation:
    """Create or update a typed relation between two media records.

    Args:
        db: Database session.
        subject_media_id: Subject media id.
        object_media_id: Object media id.
        relation_type: Typed relation.
        source: Extraction or enrichment source.
        provenance_episode_id: Optional source episode id.
        confidence: Relation confidence.
        evidence_text: Optional evidence text.
        metadata_json: Optional relation metadata.

    Returns:
        Existing or newly created media relation.
    """
    _validate_confidence(confidence)
    relation_key = stable_media_relation_key(
        subject_media_id=subject_media_id,
        object_media_id=object_media_id,
        relation_type=relation_type,
        source=source,
        provenance_episode_id=provenance_episode_id,
    )
    relation = (
        db.query(MediaRelation)
        .filter(MediaRelation.relation_key == relation_key)
        .first()
    )
    if relation is None:
        relation = MediaRelation(
            relation_key=relation_key,
            subject_media_id=subject_media_id,
            object_media_id=object_media_id,
            relation_type=relation_type.value,
            source=source,
        )
        db.add(relation)

    relation.confidence = confidence
    relation.evidence_text = _normalize_optional(evidence_text)
    relation.provenance_episode_id = provenance_episode_id
    relation.metadata_json = metadata_json
    db.flush()
    return relation


def stable_media_relation_key(
    *,
    subject_media_id: int,
    object_media_id: int,
    relation_type: MediaRelationType,
    source: str,
    provenance_episode_id: int | None = None,
) -> str:
    """Build a stable idempotency key for a media relation.

    Args:
        subject_media_id: Subject media id.
        object_media_id: Object media id.
        relation_type: Typed relation.
        source: Extraction or enrichment source.
        provenance_episode_id: Optional source episode id.

    Returns:
        Stable relation key.
    """
    return _stable_key(
        prefix="media-rel",
        parts=(
            str(subject_media_id),
            relation_type.value,
            str(object_media_id),
            _normalize_required(value=source, field_name="source"),
            str(provenance_episode_id or ""),
        ),
    )


def _validate_confidence(
    confidence: float,
) -> None:
    """Validate confidence range."""
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")


def _stable_key(
    *,
    prefix: str,
    parts: tuple[str, ...],
) -> str:
    """Build a compact stable key from string parts."""
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest[:64]}"


def _normalize_required(
    *,
    value: str,
    field_name: str,
) -> str:
    """Normalize a required string field."""
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be non-empty")
    return normalized


def _normalize_optional(
    value: str | None,
) -> str | None:
    """Normalize an optional string field."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
