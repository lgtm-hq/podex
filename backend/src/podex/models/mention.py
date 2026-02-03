"""Mention model."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.media import Media


class Mention(Base):
    """Mention model representing a media mention in an episode."""

    __tablename__ = "mentions"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)

    timestamp_seconds: Mapped[int | None] = mapped_column()
    context: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(default=1.0)

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    episode: Mapped["Episode"] = relationship(back_populates="mentions")
    media: Mapped["Media"] = relationship(back_populates="mentions")

    @property
    def youtube_timestamp_url(self) -> str | None:
        """Get YouTube URL with timestamp."""
        if self.episode.youtube_id and self.timestamp_seconds is not None:
            return f"https://youtube.com/watch?v={self.episode.youtube_id}&t={self.timestamp_seconds}"
        return None
