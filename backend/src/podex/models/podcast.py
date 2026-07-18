"""Podcast source ORM model."""

from datetime import datetime
from enum import StrEnum, auto

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base
from podex.models.episode import DiscoverySource


class PodcastStatus(StrEnum):
    """Status of a podcast in the processing workflow."""

    WATCHLIST = auto()
    ACTIVE = auto()
    PAUSED = auto()


class Podcast(Base):
    """A podcast source in the catalog."""

    __tablename__ = "podcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(2000), default=None)
    status: Mapped[PodcastStatus] = mapped_column(
        SAEnum(PodcastStatus, native_enum=False, length=20),
        default=PodcastStatus.WATCHLIST,
        server_default=PodcastStatus.WATCHLIST.value,
        index=True,
    )
    # Provider handles used by discovery to locate this podcast's feeds.
    rss_url: Mapped[str | None] = mapped_column(
        String(500),
        index=True,
        default=None,
    )
    spotify_id: Mapped[str | None] = mapped_column(
        String(50),
        index=True,
        default=None,
    )
    apple_id: Mapped[str | None] = mapped_column(String(50), default=None)
    youtube_channel_id: Mapped[str | None] = mapped_column(String(30), default=None)
    podscripts_slug: Mapped[str | None] = mapped_column(
        String(100),
        index=True,
        default=None,
    )
    discovery_source: Mapped[DiscoverySource | None] = mapped_column(
        SAEnum(DiscoverySource, native_enum=False, length=50),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
