"""Transcript model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.derivative_generation_run import DerivativeGenerationRun
    from podex.models.episode import Episode
    from podex.models.semantic_chunk import SemanticChunk


class Transcript(Base):
    """Transcript model representing stored episode transcripts."""

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    raw_text: Mapped[str | None] = mapped_column(Text)
    segments_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime | None] = mapped_column()
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    cleaned_at: Mapped[datetime | None] = mapped_column()
    retention_tier: Mapped[str] = mapped_column(String(32), default="hot", index=True)
    retention_policy_version: Mapped[str | None] = mapped_column(String(80))
    retention_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    retention_exempt_sample: Mapped[bool] = mapped_column(Boolean, default=False)
    source_retention_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_blockers_json: Mapped[list[str] | None] = mapped_column(JSON)
    digest_text: Mapped[str | None] = mapped_column(Text)
    digest_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    episode: Mapped["Episode"] = relationship(back_populates="transcripts")
    semantic_chunks: Mapped[list["SemanticChunk"]] = relationship(
        back_populates="transcript",
    )
    derivative_runs: Mapped[list["DerivativeGenerationRun"]] = relationship(
        back_populates="transcript",
    )
