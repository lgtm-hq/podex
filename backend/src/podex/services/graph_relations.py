"""Graph relation and triple persistence services."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from sqlalchemy.orm import Session

from podex.models import (
    GraphTriple,
    GraphTripleObjectKind,
    Media,
    MediaExternalRef,
    MediaExternalRefSource,
    MediaRelation,
    MediaRelationType,
)


@dataclass(frozen=True, slots=True)
class GraphTripleInputData:
    """Input for an idempotent graph triple upsert."""

    subject_media_id: int
    predicate: str
    source: str
    object_media_id: int | None = None
    object_value: str | None = None
    provenance_episode_id: int | None = None
    provenance_mention_id: int | None = None
    confidence: float = 1.0
    evidence_text: str | None = None
    metadata_json: dict[str, object] | None = None


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


def upsert_graph_triple(
    *,
    db: Session,
    payload: GraphTripleInputData,
) -> GraphTriple:
    """Create or update a graph triple with source provenance.

    Args:
        db: Database session.
        payload: Graph triple input.

    Returns:
        Existing or newly created graph triple.
    """
    _validate_graph_triple_payload(payload)
    triple_key = stable_graph_triple_key(payload)
    triple = db.query(GraphTriple).filter(GraphTriple.triple_key == triple_key).first()
    if triple is None:
        triple = GraphTriple(
            triple_key=triple_key,
            subject_media_id=payload.subject_media_id,
            predicate=_normalize_required(
                value=payload.predicate,
                field_name="predicate",
            ),
            source=_normalize_required(value=payload.source, field_name="source"),
        )
        db.add(triple)

    triple.object_media_id = payload.object_media_id
    triple.object_value = _normalize_optional(payload.object_value)
    triple.object_kind = _object_kind(payload).value
    triple.provenance_episode_id = payload.provenance_episode_id
    triple.provenance_mention_id = payload.provenance_mention_id
    triple.confidence = payload.confidence
    triple.evidence_text = _normalize_optional(payload.evidence_text)
    triple.metadata_json = payload.metadata_json
    if payload.object_media_id is not None:
        relation = upsert_media_relation(
            db=db,
            subject_media_id=payload.subject_media_id,
            object_media_id=payload.object_media_id,
            relation_type=MediaRelationType(payload.predicate),
            source=payload.source,
            provenance_episode_id=payload.provenance_episode_id,
            confidence=payload.confidence,
            evidence_text=payload.evidence_text,
            metadata_json=payload.metadata_json,
        )
        triple.media_relation_id = relation.id
    else:
        triple.media_relation_id = None
    db.flush()
    return triple


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


def stable_graph_triple_key(
    payload: GraphTripleInputData,
) -> str:
    """Build a stable idempotency key for a graph triple.

    Args:
        payload: Graph triple input.

    Returns:
        Stable graph triple key.
    """
    object_part = (
        f"media:{payload.object_media_id}"
        if payload.object_media_id is not None
        else f"literal:{_normalize_optional(payload.object_value) or ''}"
    )
    return _stable_key(
        prefix="graph-triple",
        parts=(
            str(payload.subject_media_id),
            _normalize_required(value=payload.predicate, field_name="predicate"),
            object_part,
            _normalize_required(value=payload.source, field_name="source"),
            str(payload.provenance_episode_id or ""),
            str(payload.provenance_mention_id or ""),
        ),
    )


def _validate_graph_triple_payload(
    payload: GraphTripleInputData,
) -> None:
    """Validate graph triple object and confidence constraints."""
    _validate_confidence(payload.confidence)
    has_media_object = payload.object_media_id is not None
    has_literal_object = bool(_normalize_optional(payload.object_value))
    if has_media_object == has_literal_object:
        raise ValueError("graph triple must have exactly one object")
    if has_media_object:
        MediaRelationType(payload.predicate)


def _object_kind(
    payload: GraphTripleInputData,
) -> GraphTripleObjectKind:
    """Determine graph triple object storage kind."""
    if payload.object_media_id is not None:
        return GraphTripleObjectKind.MEDIA
    return GraphTripleObjectKind.LITERAL


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
