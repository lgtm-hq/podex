"""Episode ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class Episode(Base):
    """A single podcast episode belonging to a podcast source."""

    __tablename__ = "episodes"
    __table_args__ = (
        # Serves both the podcast_id filter and the published_at ordering.
        Index("ix_episodes_podcast_id_published_at", "podcast_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    podcast_id: Mapped[int] = mapped_column(
        ForeignKey("podcasts.id", ondelete="CASCADE"),
    )
    title: Mapped[str] = mapped_column(String(500), index=True)
    episode_number: Mapped[int | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
