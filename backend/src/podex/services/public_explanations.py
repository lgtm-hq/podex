"""Public explanation-layer queries for catalog detail pages."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import Session

from podex.models import (
    DerivativeSummaryStatus,
    EpisodeSummary,
    Media,
    MediaRelation,
    MediaSummary,
    Mention,
)


@dataclass(frozen=True, slots=True)
class RelatedMediaData:
    """A related media record with relation provenance."""

    media: Media
    relation_type: str
    direction: Literal["outgoing", "incoming"]
    source: str
    confidence: float
    evidence_text: str | None
    provenance_episode_id: int | None


@dataclass(frozen=True, slots=True)
class MediaExplanationData:
    """Published explanation content for a media record."""

    derivative_summary: str | None
    related_media: list[RelatedMediaData]


@dataclass(frozen=True, slots=True)
class EpisodeExplanationData:
    """Published explanation content for an episode record."""

    derivative_summary: str | None
    mentioned_media_titles: list[str]


def get_media_explanation(
    *,
    db: Session,
    media_id: int,
) -> MediaExplanationData:
    """Load ready narrative and relation explanations for a media detail page.

    Args:
        db: Database session.
        media_id: Internal media identifier.

    Returns:
        Public explanation data for the media record.
    """
    summary = (
        db.query(MediaSummary)
        .filter(MediaSummary.media_id == media_id)
        .filter(MediaSummary.status == DerivativeSummaryStatus.READY.value)
        .order_by(MediaSummary.generated_at.desc(), MediaSummary.id.desc())
        .first()
    )
    relations = (
        db.query(MediaRelation)
        .filter(
            or_(
                MediaRelation.subject_media_id == media_id,
                MediaRelation.object_media_id == media_id,
            )
        )
        .order_by(MediaRelation.confidence.desc(), MediaRelation.id.asc())
        .all()
    )
    related: list[RelatedMediaData] = []
    for relation in relations:
        is_outgoing = relation.subject_media_id == media_id
        related_id = (
            relation.object_media_id if is_outgoing else relation.subject_media_id
        )
        item = db.query(Media).filter(Media.id == related_id).first()
        if item is not None:
            related.append(
                RelatedMediaData(
                    media=item,
                    relation_type=relation.relation_type,
                    direction="outgoing" if is_outgoing else "incoming",
                    source=relation.source,
                    confidence=relation.confidence,
                    evidence_text=relation.evidence_text,
                    provenance_episode_id=relation.provenance_episode_id,
                )
            )
    return MediaExplanationData(
        derivative_summary=summary.summary_text if summary else None,
        related_media=related,
    )


def get_episode_explanation(
    *,
    db: Session,
    episode_id: int,
) -> EpisodeExplanationData:
    """Load ready narrative and cited catalog titles for an episode detail page.

    Args:
        db: Database session.
        episode_id: Internal episode identifier.

    Returns:
        Public explanation data for the episode record.
    """
    summary = (
        db.query(EpisodeSummary)
        .filter(EpisodeSummary.episode_id == episode_id)
        .filter(EpisodeSummary.status == DerivativeSummaryStatus.READY.value)
        .order_by(EpisodeSummary.generated_at.desc(), EpisodeSummary.id.desc())
        .first()
    )
    titles = [
        title
        for (title,) in (
            db.query(Media.title)
            .join(Mention, Mention.media_id == Media.id)
            .filter(Mention.episode_id == episode_id)
            .distinct()
            .order_by(Media.title.asc())
            .all()
        )
    ]
    return EpisodeExplanationData(
        derivative_summary=summary.summary_text if summary else None,
        mentioned_media_titles=titles,
    )
