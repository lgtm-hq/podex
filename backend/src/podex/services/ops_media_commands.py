"""Shared media merge services for ops surfaces."""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_media_subquery,
    mention_count_by_media_subquery,
)
from podex.models import Media, MediaAlias, MediaAliasSourceType, MediaType, Mention
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
    source_values: list | None,
    target_values: list | None,
) -> list | None:
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
    db.delete(source_media)
    db.flush()

    merged_target = _get_ops_media_summary(db=db, media_id=target_media.id)
    if merged_target is None:
        raise RuntimeError("Merged media target could not be reloaded")

    return OpsMediaMergeResultData(
        source_id=source_media_id,
        target=merged_target,
    )
