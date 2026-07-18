"""Persisted retention configuration for transcript acquisition sources."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.podcast import Podcast


class TranscriptSourceRetentionPolicy(Base):
    """Retention thresholds and opt-out state for one podcast source."""

    __tablename__ = "transcript_source_retention_policies"
    __table_args__ = (
        UniqueConstraint(
            "podcast_id",
            "source_key",
            name="uq_transcript_source_retention_policies_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    podcast_id: Mapped[int] = mapped_column(ForeignKey("podcasts.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(80), index=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    hot_days: Mapped[int] = mapped_column(Integer)
    warm_days: Mapped[int] = mapped_column(Integer)
    min_purge_confidence: Mapped[float] = mapped_column(Float)
    source_retention_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    podcast: Mapped["Podcast"] = relationship()
