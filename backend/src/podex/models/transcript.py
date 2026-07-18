"""Transcript ORM model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode


class Transcript(Base):
    """A stored transcript for an episode.

    Retention/digest metadata is added by the retention theme; this slice
    carries only the core acquisition and cleanup fields extraction needs.
    """

    __tablename__ = "transcripts"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    provider: Mapped[str] = mapped_column(String(50))
    raw_text: Mapped[str | None] = mapped_column(Text)
    segments_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime | None] = mapped_column()
    cleaned_text: Mapped[str | None] = mapped_column(Text)
    cleaned_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    episode: Mapped["Episode"] = relationship(back_populates="transcripts")
