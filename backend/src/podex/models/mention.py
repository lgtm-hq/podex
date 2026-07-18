"""Mention ORM model linking media to episodes."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class Mention(Base):
    """An occurrence of a media item referenced within an episode."""

    __tablename__ = "mentions"
    __table_args__ = (
        Index("ix_mentions_episode_media", "episode_id", "media_id"),
        Index("ix_mentions_media_id_episode_id", "media_id", "episode_id"),
        Index(
            "ix_mentions_episode_id_timestamp_seconds",
            "episode_id",
            "timestamp_seconds",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"),
        index=True,
    )
    media_id: Mapped[int] = mapped_column(
        ForeignKey("media.id", ondelete="CASCADE"),
        index=True,
    )
    timestamp_seconds: Mapped[int | None] = mapped_column(default=None)
    context: Mapped[str | None] = mapped_column(String(2000), default=None)
    confidence: Mapped[float | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
