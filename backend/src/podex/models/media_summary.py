"""Media derivative summary model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base
from podex.models.derivative_summary import DerivativeSummaryStatus

if TYPE_CHECKING:
    from podex.models.media import Media


class MediaSummary(Base):
    """Read-optimized narrative derivative for a media entity."""

    __tablename__ = "media_summaries"
    __table_args__ = (
        UniqueConstraint("summary_key", name="uq_media_summaries_summary_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    summary_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    summary_kind: Mapped[str] = mapped_column(String(32), index=True)
    pipeline_version: Mapped[str] = mapped_column(String(80), index=True)
    prompt_version: Mapped[str] = mapped_column(String(80), index=True)
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_model: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        default=DerivativeSummaryStatus.READY.value,
        index=True,
    )
    summary_text: Mapped[str] = mapped_column(Text)
    short_summary: Mapped[str | None] = mapped_column(Text)
    highlights_json: Mapped[list[str] | None] = mapped_column(JSON)
    citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    media: Mapped[Media] = relationship(back_populates="summaries")
