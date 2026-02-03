"""Podcast model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode


class PodcastStatus(StrEnum):
    """Status of a podcast in the processing workflow."""

    WATCHLIST = auto()  # Saved for later, no processing
    ACTIVE = auto()  # Actively discovering and processing
    PAUSED = auto()  # Temporarily stopped


class Podcast(Base):
    """Podcast model representing a podcast show."""

    __tablename__ = "podcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    # Status for processing workflow
    status: Mapped[str] = mapped_column(String(20), default="watchlist")

    # Discovery source identifiers
    rss_url: Mapped[str | None] = mapped_column(String(500), index=True)
    spotify_id: Mapped[str | None] = mapped_column(String(50), index=True)
    apple_id: Mapped[str | None] = mapped_column(String(50))
    youtube_channel_id: Mapped[str | None] = mapped_column(String(30))
    podscripts_slug: Mapped[str | None] = mapped_column(String(100), index=True)

    # Where we first discovered this podcast
    discovery_source: Mapped[str | None] = mapped_column(String(50))

    episodes: Mapped[list["Episode"]] = relationship(back_populates="podcast")
