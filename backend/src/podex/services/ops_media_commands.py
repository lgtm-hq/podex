"""Shared media merge services for ops surfaces."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_media_subquery,
    mention_count_by_media_subquery,
)
from podex.models import (
    Media,
    MediaAlias,
    MediaAliasSourceType,
    MediaExternalRef,
    MediaExternalRefSource,
    MediaRelation,
    MediaType,
    Mention,
)
from podex.services.graph_relations import upsert_media_external_ref
from podex.services.media_alias_repository import ensure_media_alias
from podex.services.media_aliases import normalize_media_alias


@dataclass(frozen=True, slots=True)
class OpsMergedMediaSummaryData:
    """Merged media summary for ops mutation responses."""

    id: int
    type: MediaType
    title: str
    author: str | None
    cover_url: str | None
    year: int | None
    description: str | None
    mention_count: int
    episode_count: int


@dataclass(frozen=True, slots=True)
class OpsMediaMergeResultData:
    """Merge result for ops media mutation responses."""

    source_id: int
    target: OpsMergedMediaSummaryData


@dataclass(frozen=True, slots=True)
class OpsMediaMergeFieldChangeData:
    """Field value that would be copied from source to target during merge."""

    field: str
    source_value: Any
    target_value: Any
    merged_value: Any


@dataclass(frozen=True, slots=True)
class OpsMediaMergeAliasChangeData:
    """Alias that would be attached to the merge target."""

    alias: str
    normalized_alias: str
    source: MediaAliasSourceType


@dataclass(frozen=True, slots=True)
class OpsMediaMergePreviewData:
    """Merge preview for ops canonicalization workflows."""

    source: OpsMergedMediaSummaryData
    target: OpsMergedMediaSummaryData
    field_changes: list[OpsMediaMergeFieldChangeData]
    alias_additions: list[OpsMediaMergeAliasChangeData]
    mentions_to_move: int


@dataclass(frozen=True, slots=True)
class OpsMediaAliasData:
    """Alias attached to a canonical media record."""

    alias: str
    normalized_alias: str
    source: str
    is_primary: bool


@dataclass(frozen=True, slots=True)
class OpsMediaExternalRefData:
    """External reference attached to a canonical media record."""

    source: str
    external_id: str
    url: str | None
    label: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class OpsMediaRelationData:
    """Typed relationship involving a canonical media record."""

    direction: str
    relation_type: str
    related_media: OpsMergedMediaSummaryData
    source: str
    confidence: float


@dataclass(frozen=True, slots=True)
class OpsMediaMentionData:
    """Published mention available for media split recovery."""

    id: int
    episode_id: int
    episode_title: str
    timestamp_seconds: int | None
    context: str | None
    confidence: float


@dataclass(frozen=True, slots=True)
class OpsMediaDetailData:
    """Canonical media record details for ops management."""

    summary: OpsMergedMediaSummaryData
    google_books_id: str | None
    open_library_id: str | None
    imdb_id: str | None
    tmdb_id: int | None
    wikipedia_id: str | None
    pubmed_id: str | None
    doi: str | None
    semantic_scholar_id: str | None
    metadata_json: dict[str, Any] | None
    verification_sources: list[str]
    aliases: list[OpsMediaAliasData]
    external_refs: list[OpsMediaExternalRefData]
    relations: list[OpsMediaRelationData]
    mentions: list[OpsMediaMentionData]


@dataclass(frozen=True, slots=True)
class UpdateOpsMediaInputData:
    """Partial editable fields for canonical media correction."""

    provided_fields: frozenset[str] = frozenset()
    type: MediaType | None = None
    title: str | None = None
    author: str | None = None
    cover_url: str | None = None
    year: int | None = None
    description: str | None = None
    google_books_id: str | None = None
    open_library_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    wikipedia_id: str | None = None
    pubmed_id: str | None = None
    doi: str | None = None
    semantic_scholar_id: str | None = None
    metadata_json: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class UpsertOpsMediaExternalRefInputData:
    """External reference fields provided by an operator."""

    source: MediaExternalRefSource
    external_id: str
    url: str | None = None
    label: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class SplitOpsMediaInputData:
    """Replacement media and mention assignment for split recovery."""

    mention_ids: tuple[int, ...]
    type: MediaType
    title: str
    author: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class OpsMediaSplitResultData:
    """Original and newly created records after media split recovery."""

    source: OpsMediaDetailData
    created: OpsMediaDetailData
    mentions_moved: int


def _get_ops_media_summary(
    *,
    db: Session,
    media_id: int,
) -> OpsMergedMediaSummaryData | None:
    """Load a merged media summary with aggregated counts.

    Args:
        db: Database session.
        media_id: Internal media identifier.

    Returns:
        Merged media summary when found, otherwise ``None``.
    """
    mention_counts = mention_count_by_media_subquery(db)
    episode_counts = episode_count_by_media_subquery(db)
    row = (
        db.query(
            Media,
            func.coalesce(mention_counts.c.mention_count, 0).label("mention_count"),
            func.coalesce(episode_counts.c.episode_count, 0).label("episode_count"),
        )
        .outerjoin(mention_counts, Media.id == mention_counts.c.media_id)
        .outerjoin(episode_counts, Media.id == episode_counts.c.media_id)
        .filter(Media.id == media_id)
        .first()
    )
    if row is None:
        return None

    media, mention_count, episode_count = row
    return OpsMergedMediaSummaryData(
        id=media.id,
        type=MediaType(media.type),
        title=media.title,
        author=media.author,
        cover_url=media.cover_url,
        year=media.year,
        description=media.description,
        mention_count=mention_count,
        episode_count=episode_count,
    )


def _merge_verification_sources(
    *,
    source_values: list[str] | None,
    target_values: list[str] | None,
) -> list[str] | None:
    """Merge verification source lists while preserving target precedence.

    Args:
        source_values: Verification sources from the source media record.
        target_values: Verification sources from the target media record.

    Returns:
        Combined verification source list when any values exist.
    """
    if not source_values and not target_values:
        return target_values

    combined = list(target_values or [])
    for value in source_values or []:
        if value not in combined:
            combined.append(value)
    return combined


def _merge_scalar_field_names() -> tuple[str, ...]:
    """Return scalar media fields merged by target-gap filling."""
    return (
        "author",
        "cover_url",
        "year",
        "description",
        "google_books_id",
        "open_library_id",
        "imdb_id",
        "tmdb_id",
        "wikipedia_id",
        "pubmed_id",
        "doi",
        "semantic_scholar_id",
        "enriched_at",
        "enrichment_source",
        "enrichment_confidence",
    )


def get_ops_media_detail(
    *,
    db: Session,
    media_id: int,
) -> OpsMediaDetailData | None:
    """Load canonical media record detail for operator management.

    Args:
        db: Database session.
        media_id: Internal media identifier.

    Returns:
        Media detail when found, otherwise ``None``.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    summary = _get_ops_media_summary(db=db, media_id=media_id)
    if media is None or summary is None:
        return None

    aliases = (
        db.query(MediaAlias)
        .filter(MediaAlias.media_id == media_id)
        .order_by(MediaAlias.is_primary.desc(), MediaAlias.id.asc())
        .all()
    )
    external_refs = (
        db.query(MediaExternalRef)
        .filter(MediaExternalRef.media_id == media_id)
        .order_by(MediaExternalRef.source.asc(), MediaExternalRef.id.asc())
        .all()
    )
    mentions = (
        db.query(Mention)
        .filter(Mention.media_id == media_id)
        .order_by(Mention.id.asc())
        .all()
    )
    relations: list[OpsMediaRelationData] = []
    for relation in (
        db.query(MediaRelation)
        .filter(MediaRelation.subject_media_id == media_id)
        .order_by(MediaRelation.id.asc())
        .all()
    ):
        related = _get_ops_media_summary(db=db, media_id=relation.object_media_id)
        if related is not None:
            relations.append(
                OpsMediaRelationData(
                    direction="outgoing",
                    relation_type=relation.relation_type,
                    related_media=related,
                    source=relation.source,
                    confidence=relation.confidence,
                )
            )
    for relation in (
        db.query(MediaRelation)
        .filter(MediaRelation.object_media_id == media_id)
        .order_by(MediaRelation.id.asc())
        .all()
    ):
        related = _get_ops_media_summary(db=db, media_id=relation.subject_media_id)
        if related is not None:
            relations.append(
                OpsMediaRelationData(
                    direction="incoming",
                    relation_type=relation.relation_type,
                    related_media=related,
                    source=relation.source,
                    confidence=relation.confidence,
                )
            )

    return OpsMediaDetailData(
        summary=summary,
        google_books_id=media.google_books_id,
        open_library_id=media.open_library_id,
        imdb_id=media.imdb_id,
        tmdb_id=media.tmdb_id,
        wikipedia_id=media.wikipedia_id,
        pubmed_id=media.pubmed_id,
        doi=media.doi,
        semantic_scholar_id=media.semantic_scholar_id,
        metadata_json=media.metadata_json,
        verification_sources=list(media.verification_sources or []),
        aliases=[
            OpsMediaAliasData(
                alias=alias.alias,
                normalized_alias=alias.normalized_alias,
                source=alias.source,
                is_primary=alias.is_primary,
            )
            for alias in aliases
        ],
        external_refs=[
            OpsMediaExternalRefData(
                source=reference.source,
                external_id=reference.external_id,
                url=reference.url,
                label=reference.label,
                description=reference.description,
            )
            for reference in external_refs
        ],
        relations=relations,
        mentions=[
            OpsMediaMentionData(
                id=mention.id,
                episode_id=mention.episode_id,
                episode_title=mention.episode.title,
                timestamp_seconds=mention.timestamp_seconds,
                context=mention.context,
                confidence=mention.confidence,
            )
            for mention in mentions
        ],
    )


def update_ops_media(
    *,
    db: Session,
    media_id: int,
    payload: UpdateOpsMediaInputData,
) -> OpsMediaDetailData | None:
    """Update editable canonical media fields.

    Args:
        db: Database session.
        media_id: Internal media identifier.
        payload: Partial field update payload.

    Returns:
        Updated media detail when found, otherwise ``None``.

    Raises:
        ValueError: If a required display field is cleared.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if media is None:
        return None

    old_title = media.title
    if "title" in payload.provided_fields:
        if payload.title is None or not payload.title.strip():
            raise ValueError("title cannot be cleared")
        media.title = payload.title.strip()
    if "type" in payload.provided_fields:
        if payload.type is None:
            raise ValueError("type cannot be cleared")
        media.type = payload.type.value

    for field_name in (
        "author",
        "cover_url",
        "year",
        "description",
        "google_books_id",
        "open_library_id",
        "imdb_id",
        "tmdb_id",
        "wikipedia_id",
        "pubmed_id",
        "doi",
        "semantic_scholar_id",
        "metadata_json",
    ):
        if field_name in payload.provided_fields:
            setattr(media, field_name, getattr(payload, field_name))

    if media.title != old_title:
        ensure_media_alias(
            db=db,
            media=media,
            alias=old_title,
            source=MediaAliasSourceType.MANUAL,
        )
    ensure_media_alias(
        db=db,
        media=media,
        alias=media.title,
        source=MediaAliasSourceType.MANUAL,
        is_primary=True,
    )
    db.flush()
    return get_ops_media_detail(db=db, media_id=media_id)


def add_ops_media_alias(
    *,
    db: Session,
    media_id: int,
    alias: str,
) -> OpsMediaDetailData | None:
    """Add one normalized alias to a canonical media record.

    Args:
        db: Database session.
        media_id: Internal media identifier.
        alias: Operator-provided alternate title.

    Returns:
        Updated media detail when found, otherwise ``None``.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if media is None:
        return None
    ensure_media_alias(
        db=db,
        media=media,
        alias=alias,
        source=MediaAliasSourceType.MANUAL,
    )
    db.flush()
    return get_ops_media_detail(db=db, media_id=media_id)


def upsert_ops_media_external_ref(
    *,
    db: Session,
    media_id: int,
    payload: UpsertOpsMediaExternalRefInputData,
) -> OpsMediaDetailData | None:
    """Add or update one canonical media external reference.

    Args:
        db: Database session.
        media_id: Internal media identifier.
        payload: External reference details.

    Returns:
        Updated media detail when found, otherwise ``None``.
    """
    media = db.query(Media).filter(Media.id == media_id).first()
    if media is None:
        return None
    upsert_media_external_ref(
        db=db,
        media=media,
        source=payload.source,
        external_id=payload.external_id,
        url=payload.url,
        label=payload.label,
        description=payload.description,
    )
    db.flush()
    return get_ops_media_detail(db=db, media_id=media_id)


def split_ops_media(
    *,
    db: Session,
    media_id: int,
    payload: SplitOpsMediaInputData,
) -> OpsMediaSplitResultData | None:
    """Move selected mentions from a canonical record into a new record.

    Args:
        db: Database session.
        media_id: Existing canonical record to correct.
        payload: New record metadata and mentions to move.

    Returns:
        Updated source and newly created record when found, otherwise ``None``.

    Raises:
        RuntimeError: If resulting detail cannot be loaded.
        ValueError: If no valid mentions are selected or the title is empty.
    """
    source = db.query(Media).filter(Media.id == media_id).first()
    if source is None:
        return None
    if not payload.title.strip():
        raise ValueError("title cannot be empty")
    if not payload.mention_ids:
        raise ValueError("At least one mention must be selected")

    mentions = (
        db.query(Mention)
        .filter(Mention.id.in_(payload.mention_ids))
        .filter(Mention.media_id == media_id)
        .all()
    )
    if len(mentions) != len(set(payload.mention_ids)):
        raise ValueError("Selected mentions must belong to the source media")

    created_media = Media(
        type=payload.type.value,
        title=payload.title.strip(),
        author=payload.author.strip() if payload.author else None,
        description=payload.description.strip() if payload.description else None,
    )
    db.add(created_media)
    db.flush()
    ensure_media_alias(
        db=db,
        media=created_media,
        alias=created_media.title,
        source=MediaAliasSourceType.MANUAL,
        is_primary=True,
    )
    for mention in mentions:
        mention.media = created_media
    db.flush()

    source_detail = get_ops_media_detail(db=db, media_id=media_id)
    created_detail = get_ops_media_detail(db=db, media_id=created_media.id)
    if source_detail is None or created_detail is None:
        raise RuntimeError("Split media records could not be reloaded")
    return OpsMediaSplitResultData(
        source=source_detail,
        created=created_detail,
        mentions_moved=len(mentions),
    )


def _load_alias_norms(
    *,
    db: Session,
    media_id: int,
) -> set[str]:
    """Load normalized alias values for a media record."""
    return {
        normalized_alias
        for (normalized_alias,) in db.query(MediaAlias.normalized_alias)
        .filter(MediaAlias.media_id == media_id)
        .all()
    }


def _preview_alias_additions(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> list[OpsMediaMergeAliasChangeData]:
    """Build alias additions that would result from a media merge."""
    target_norms = _load_alias_norms(db=db, media_id=target_media.id)
    additions: list[OpsMediaMergeAliasChangeData] = []

    source_values: list[tuple[str, MediaAliasSourceType]] = [
        (source_media.title, MediaAliasSourceType.MERGE),
    ]
    source_values.extend(
        (alias.alias, MediaAliasSourceType(alias.source))
        for alias in db.query(MediaAlias)
        .filter(MediaAlias.media_id == source_media.id)
        .order_by(MediaAlias.is_primary.desc(), MediaAlias.id.asc())
        .all()
    )

    seen_norms: set[str] = set()
    for alias_value, source in source_values:
        normalized_alias = normalize_media_alias(alias_value)
        if normalized_alias is None:
            continue
        if normalized_alias in target_norms or normalized_alias in seen_norms:
            continue
        seen_norms.add(normalized_alias)
        additions.append(
            OpsMediaMergeAliasChangeData(
                alias=alias_value.strip(),
                normalized_alias=normalized_alias,
                source=source,
            )
        )

    return additions


def _move_source_aliases_to_target(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> None:
    """Move source aliases onto the surviving target and drop duplicates."""
    ensure_media_alias(
        db=db,
        media=target_media,
        alias=target_media.title,
        source=MediaAliasSourceType.MERGE,
        is_primary=True,
    )
    ensure_media_alias(
        db=db,
        media=target_media,
        alias=source_media.title,
        source=MediaAliasSourceType.MERGE,
    )

    target_norms = _load_alias_norms(db=db, media_id=target_media.id)
    source_aliases = (
        db.query(MediaAlias)
        .filter(MediaAlias.media_id == source_media.id)
        .order_by(MediaAlias.is_primary.desc(), MediaAlias.id.asc())
        .all()
    )
    for alias in source_aliases:
        if alias.normalized_alias in target_norms:
            db.delete(alias)
            continue
        alias.media = target_media
        alias.source = MediaAliasSourceType.MERGE.value
        alias.is_primary = False
        target_norms.add(alias.normalized_alias)


def preview_ops_media_merge(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> OpsMediaMergePreviewData | None:
    """Preview merge effects without mutating media records.

    Args:
        db: Database session.
        source_media_id: Internal media identifier to merge from.
        target_media_id: Internal media identifier to merge into.

    Returns:
        Merge preview when both media records exist, otherwise ``None``.

    Raises:
        ValueError: If the merge request is invalid.
    """
    if source_media_id == target_media_id:
        raise ValueError("Source and target media must differ")

    source_media = db.query(Media).filter(Media.id == source_media_id).first()
    target_media = db.query(Media).filter(Media.id == target_media_id).first()
    if source_media is None or target_media is None:
        return None

    if source_media.type != target_media.type:
        raise ValueError("Media types must match for merge")

    source_summary = _get_ops_media_summary(db=db, media_id=source_media.id)
    target_summary = _get_ops_media_summary(db=db, media_id=target_media.id)
    if source_summary is None or target_summary is None:
        return None

    return OpsMediaMergePreviewData(
        source=source_summary,
        target=target_summary,
        field_changes=[
            OpsMediaMergeFieldChangeData(
                field=field_name,
                source_value=getattr(source_media, field_name),
                target_value=getattr(target_media, field_name),
                merged_value=getattr(source_media, field_name),
            )
            for field_name in _merge_scalar_field_names()
            if getattr(target_media, field_name) is None
            and getattr(source_media, field_name) is not None
        ],
        alias_additions=_preview_alias_additions(
            db=db,
            source_media=source_media,
            target_media=target_media,
        ),
        mentions_to_move=db.query(Mention)
        .filter(Mention.media_id == source_media.id)
        .count(),
    )


def merge_ops_media(
    *,
    db: Session,
    source_media_id: int,
    target_media_id: int,
) -> OpsMediaMergeResultData | None:
    """Merge one media record into another for ops catalog maintenance.

    Args:
        db: Database session.
        source_media_id: Internal media identifier to merge from.
        target_media_id: Internal media identifier to merge into.

    Returns:
        Merge result when both media records exist, otherwise ``None``.

    Raises:
        RuntimeError: If the merged target cannot be reloaded.
        ValueError: If the merge request is invalid.
    """
    if source_media_id == target_media_id:
        raise ValueError("Source and target media must differ")

    source_media = db.query(Media).filter(Media.id == source_media_id).first()
    target_media = db.query(Media).filter(Media.id == target_media_id).first()
    if source_media is None or target_media is None:
        return None

    if source_media.type != target_media.type:
        raise ValueError("Media types must match for merge")

    for field_name in _merge_scalar_field_names():
        if (
            getattr(target_media, field_name) is None
            and getattr(
                source_media,
                field_name,
            )
            is not None
        ):
            setattr(target_media, field_name, getattr(source_media, field_name))

    if source_media.metadata_json:
        target_media.metadata_json = {
            **source_media.metadata_json,
            **(target_media.metadata_json or {}),
        }

    target_media.verification_sources = _merge_verification_sources(
        source_values=source_media.verification_sources,
        target_values=target_media.verification_sources,
    )
    target_media.doi_verified = bool(
        target_media.doi_verified or source_media.doi_verified,
    )

    mentions = db.query(Mention).filter(Mention.media_id == source_media.id).all()
    for mention in mentions:
        mention.media = target_media

    _move_source_aliases_to_target(
        db=db,
        source_media=source_media,
        target_media=target_media,
    )
    _repoint_derivatives_to_target(
        db=db,
        source_media=source_media,
        target_media=target_media,
    )
    db.delete(source_media)
    db.flush()

    merged_target = _get_ops_media_summary(db=db, media_id=target_media.id)
    if merged_target is None:
        raise RuntimeError("Merged media target could not be reloaded")

    return OpsMediaMergeResultData(
        source_id=source_media_id,
        target=merged_target,
    )


def _repoint_derivatives_to_target(
    *,
    db: Session,
    source_media: Media,
    target_media: Media,
) -> None:
    """Re-point derivative data from a merged source onto the target.

    External references move (deduplicated against the target), relations
    and graph triples are re-keyed onto the target (self-references and key
    collisions are dropped), and source media summaries are deleted so the
    derivative pipeline regenerates them for the canonical record (#98).

    Args:
        db: Database session.
        source_media: Media record being merged away.
        target_media: Surviving canonical media record.
    """
    from podex.models import (
        GraphTriple,
        MediaRelation,
        MediaRelationType,
        MediaSummary,
    )
    from podex.services.graph_relations import (
        GraphTripleInputData,
        stable_graph_triple_key,
        stable_media_relation_key,
    )

    for ref in list(source_media.external_refs):
        duplicate = (
            db.query(MediaExternalRef)
            .filter(
                MediaExternalRef.media_id == target_media.id,
                MediaExternalRef.source == ref.source,
                MediaExternalRef.external_id == ref.external_id,
            )
            .first()
        )
        if duplicate is not None:
            db.delete(ref)
        else:
            # Reassign via the relationship so deleting the source does not
            # null the FK through the collection cascade.
            ref.media = target_media

    relations = (
        db.query(MediaRelation)
        .filter(
            (MediaRelation.subject_media_id == source_media.id)
            | (MediaRelation.object_media_id == source_media.id),
        )
        .all()
    )
    for relation in relations:
        subject_id = (
            target_media.id
            if relation.subject_media_id == source_media.id
            else relation.subject_media_id
        )
        object_id = (
            target_media.id
            if relation.object_media_id == source_media.id
            else relation.object_media_id
        )
        if subject_id == object_id:
            db.delete(relation)
            continue
        new_key = stable_media_relation_key(
            subject_media_id=subject_id,
            object_media_id=object_id,
            relation_type=MediaRelationType(relation.relation_type),
            source=relation.source,
            provenance_episode_id=relation.provenance_episode_id,
        )
        collision = (
            db.query(MediaRelation)
            .filter(
                MediaRelation.relation_key == new_key,
                MediaRelation.id != relation.id,
            )
            .first()
        )
        if collision is not None:
            db.delete(relation)
            continue
        relation.subject_media = (
            target_media
            if relation.subject_media_id == source_media.id
            else relation.subject_media
        )
        relation.object_media = (
            target_media
            if relation.object_media_id == source_media.id
            else relation.object_media
        )
        relation.relation_key = new_key

    triples = (
        db.query(GraphTriple)
        .filter(
            (GraphTriple.subject_media_id == source_media.id)
            | (GraphTriple.object_media_id == source_media.id),
        )
        .all()
    )
    for triple in triples:
        subject_id = (
            target_media.id
            if triple.subject_media_id == source_media.id
            else triple.subject_media_id
        )
        object_id = (
            target_media.id
            if triple.object_media_id == source_media.id
            else triple.object_media_id
        )
        if object_id is not None and subject_id == object_id:
            db.delete(triple)
            continue
        new_key = stable_graph_triple_key(
            GraphTripleInputData(
                subject_media_id=subject_id,
                predicate=triple.predicate,
                source=triple.source,
                object_media_id=object_id,
                object_value=triple.object_value,
                provenance_episode_id=triple.provenance_episode_id,
                provenance_mention_id=triple.provenance_mention_id,
            ),
        )
        collision = (
            db.query(GraphTriple)
            .filter(
                GraphTriple.triple_key == new_key,
                GraphTriple.id != triple.id,
            )
            .first()
        )
        if collision is not None:
            db.delete(triple)
            continue
        if triple.subject_media_id == source_media.id:
            triple.subject_media = target_media
        if triple.object_media_id == source_media.id:
            triple.object_media = target_media
        triple.triple_key = new_key

    for summary in (
        db.query(MediaSummary).filter(MediaSummary.media_id == source_media.id).all()
    ):
        db.delete(summary)

    db.flush()
