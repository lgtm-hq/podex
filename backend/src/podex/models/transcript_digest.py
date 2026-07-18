"""Durable proof-of-processing records for purged raw transcripts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.transcript import Transcript


class TranscriptDigest(Base):
    """Proof record retained when a raw transcript payload is deleted."""

    __tablename__ = "transcript_digests"
    __table_args__ = (
        UniqueConstraint("digest_key", name="uq_transcript_digests_digest_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id"), index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    digest_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    policy_version: Mapped[str | None] = mapped_column(String(80))
    summary_text: Mapped[str] = mapped_column(Text)
    sampling_strata_json: Mapped[dict[str, str] | None] = mapped_column(JSON)
    extraction_versions_json: Mapped[list[str] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    transcript: Mapped[Transcript] = relationship(back_populates="digests")
    episode: Mapped[Episode] = relationship()
