"""Shared media merge services for ops surfaces."""

from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from podex.api.query_helpers import (
    episode_count_by_media_subquery,
    mention_count_by_media_subquery,
)
from podex.models import Media, MediaType, Mention


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

    scalar_fields = (
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
    for field_name in scalar_fields:
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

    db.delete(source_media)
    db.flush()

    merged_target = _get_ops_media_summary(db=db, media_id=target_media.id)
    if merged_target is None:
        raise RuntimeError("Merged media target could not be reloaded")

    return OpsMediaMergeResultData(
        source_id=source_media_id,
        target=merged_target,
    )
