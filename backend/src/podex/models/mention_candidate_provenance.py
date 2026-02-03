"""Mention candidate provenance model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media import Media
    from podex.models.mention_candidate import MentionCandidate
    from podex.models.transcription_job import TranscriptionJob


class MentionCandidateProvenanceEventType(StrEnum):
    """Event types recorded for candidate extraction provenance."""

    CREATED = auto()
    UPDATED = auto()


class MentionCandidateProvenance(Base):
    """Immutable provenance snapshot for a mention candidate extraction event."""

    __tablename__ = "mention_candidate_provenance"

    id: Mapped[int] = mapped_column(primary_key=True)
    mention_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("mention_candidates.id"),
        index=True,
    )
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcription_jobs.id"),
        index=True,
    )
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    change_summary: Mapped[str | None] = mapped_column(Text)
    raw_title: Mapped[str] = mapped_column(String(500))
    normalized_title: Mapped[str | None] = mapped_column(String(500))
    suggested_author: Mapped[str | None] = mapped_column(String(255))
    timestamp_seconds: Mapped[int | None] = mapped_column()
    context: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extraction_source: Mapped[str | None] = mapped_column(String(50))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    mention_candidate: Mapped["MentionCandidate"] = relationship(
        back_populates="provenance_events",
    )
    source_job: Mapped["TranscriptionJob | None"] = relationship()
    media: Mapped["Media | None"] = relationship()
