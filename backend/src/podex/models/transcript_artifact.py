"""Stored raw transcript artifact metadata."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.transcript import Transcript
    from podex.models.transcript_digest import TranscriptDigest


class TranscriptArtifact(Base):
    """Private encrypted raw transcript payload stored outside PostgreSQL."""

    __tablename__ = "transcript_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id"), index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    reacquired_from_digest_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcript_digests.id"),
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    storage_backend: Mapped[str] = mapped_column(String(32))
    encryption_key_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(50))
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    provenance_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    stored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    transcript: Mapped["Transcript"] = relationship(back_populates="artifacts")
    episode: Mapped["Episode"] = relationship()
    reacquired_from_digest: Mapped["TranscriptDigest | None"] = relationship()
