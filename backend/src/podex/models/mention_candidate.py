"""Mention candidate model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.media import Media
    from podex.models.mention import Mention
    from podex.models.mention_candidate_provenance import MentionCandidateProvenance
    from podex.models.review_item import ReviewItem


class MentionCandidateState(StrEnum):
    """Lifecycle states for extracted mention candidates."""

    PENDING_REVIEW = auto()
    PUBLISHED = auto()
    REJECTED = auto()
    MERGED = auto()
    SPLIT = auto()


class MentionCandidate(Base):
    """Candidate mention extracted from an episode before publication."""

    __tablename__ = "mention_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id"), index=True)
    mention_id: Mapped[int | None] = mapped_column(
        ForeignKey("mentions.id"),
        unique=True,
        index=True,
    )
    media_type: Mapped[str] = mapped_column(String(20), index=True)
    raw_title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str | None] = mapped_column(String(500))
    suggested_author: Mapped[str | None] = mapped_column(String(255))
    timestamp_seconds: Mapped[int | None] = mapped_column()
    context: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_source: Mapped[str | None] = mapped_column(String(50))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(
        String(32),
        index=True,
        default=MentionCandidateState.PENDING_REVIEW.value,
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column()

    episode: Mapped["Episode"] = relationship()
    media: Mapped["Media | None"] = relationship()
    mention: Mapped["Mention | None"] = relationship()
    review_item: Mapped["ReviewItem | None"] = relationship(
        back_populates="mention_candidate",
        uselist=False,
    )
    provenance_events: Mapped[list["MentionCandidateProvenance"]] = relationship(
        back_populates="mention_candidate",
        order_by="desc(MentionCandidateProvenance.created_at)",
    )
