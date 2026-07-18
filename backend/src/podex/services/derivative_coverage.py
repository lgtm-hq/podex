"""Derivative coverage gates for raw transcript retention."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryStatus,
    Episode,
    EpisodeSummary,
    GraphTriple,
    MediaSummary,
    Mention,
    SemanticChunk,
)


class PublicDerivativeQueryClass(StrEnum):
    """Public query classes that must survive raw transcript purge."""

    EPISODE_DETAIL = auto()
    EPISODE_SNIPPETS = auto()
    GLOBAL_SEARCH = auto()
    MEDIA_DETAIL = auto()
    MEDIA_MENTIONS = auto()
    RELATED_ENTITIES = auto()
    TRENDS = auto()


@dataclass(frozen=True, slots=True)
class DerivativeCoverageResultData:
    """Derivative coverage result for a single episode."""

    episode_id: int
    purge_safe: bool
    covered_query_classes: tuple[PublicDerivativeQueryClass, ...]
    missing_query_classes: tuple[PublicDerivativeQueryClass, ...]
    missing_reasons: dict[str, str]


def evaluate_episode_derivative_coverage(
    *,
    db: Session,
    episode: Episode,
) -> DerivativeCoverageResultData:
    """Evaluate whether derivatives cover public queries for an episode.

    Args:
        db: Database session.
        episode: Episode whose raw transcript may become purge eligible.

    Returns:
        Coverage result used to gate raw transcript purge.
    """
    coverage = _coverage_state(db=db, episode=episode)
    missing_reasons: dict[PublicDerivativeQueryClass, str] = {}
    if not coverage["has_episode_summary"]:
        missing_reasons[PublicDerivativeQueryClass.EPISODE_DETAIL] = (
            "missing ready episode summary"
        )
    if not coverage["has_semantic_chunks"]:
        missing_reasons[PublicDerivativeQueryClass.EPISODE_SNIPPETS] = (
            "missing semantic chunks"
        )
    if not (
        coverage["has_semantic_chunks"]
        and coverage["has_episode_summary"]
        and coverage["all_media_summarized"]
    ):
        missing_reasons[PublicDerivativeQueryClass.GLOBAL_SEARCH] = (
            "missing summary or semantic chunk derivatives"
        )
    if not coverage["all_media_summarized"]:
        missing_reasons[PublicDerivativeQueryClass.MEDIA_DETAIL] = (
            "missing ready summaries for mentioned media"
        )
    if not coverage["has_mentions"]:
        missing_reasons[PublicDerivativeQueryClass.MEDIA_MENTIONS] = (
            "missing structured mentions"
        )
    if not coverage["has_graph_triples"]:
        missing_reasons[PublicDerivativeQueryClass.RELATED_ENTITIES] = (
            "missing graph triples"
        )
    if not coverage["has_mentions"]:
        missing_reasons[PublicDerivativeQueryClass.TRENDS] = (
            "missing structured mentions for trend counts"
        )

    missing = tuple(missing_reasons)
    covered = tuple(
        query_class
        for query_class in PublicDerivativeQueryClass
        if query_class not in missing_reasons
    )
    return DerivativeCoverageResultData(
        episode_id=episode.id,
        purge_safe=not missing,
        covered_query_classes=covered,
        missing_query_classes=missing,
        missing_reasons={
            query_class.value: reason for query_class, reason in missing_reasons.items()
        },
    )


def _coverage_state(
    *,
    db: Session,
    episode: Episode,
) -> dict[str, bool]:
    """Collect derivative coverage booleans for an episode."""
    mentioned_media_ids = [
        media_id
        for (media_id,) in db.query(Mention.media_id)
        .filter(Mention.episode_id == episode.id)
        .distinct()
        .all()
    ]
    summarized_media_ids = {
        media_id
        for (media_id,) in db.query(MediaSummary.media_id)
        .filter(MediaSummary.media_id.in_(mentioned_media_ids or [-1]))
        .filter(MediaSummary.status == DerivativeSummaryStatus.READY.value)
        .distinct()
        .all()
    }
    return {
        "has_episode_summary": db.query(EpisodeSummary)
        .filter(EpisodeSummary.episode_id == episode.id)
        .filter(EpisodeSummary.status == DerivativeSummaryStatus.READY.value)
        .first()
        is not None,
        "has_semantic_chunks": db.query(SemanticChunk)
        .filter(SemanticChunk.episode_id == episode.id)
        .first()
        is not None,
        "has_mentions": bool(mentioned_media_ids),
        "all_media_summarized": bool(mentioned_media_ids)
        and set(mentioned_media_ids).issubset(summarized_media_ids),
        "has_graph_triples": db.query(GraphTriple)
        .filter(GraphTriple.provenance_episode_id == episode.id)
        .first()
        is not None,
    }
