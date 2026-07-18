"""Episode ORM model."""

from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.derivative_generation_run import DerivativeGenerationRun
    from podex.models.episode_summary import EpisodeSummary
    from podex.models.podcast import Podcast
    from podex.models.semantic_chunk import SemanticChunk
    from podex.models.transcript import Transcript


class DiscoverySource(StrEnum):
    """Where an episode or podcast was first discovered."""

    PODSCRIPTS = auto()
    RSS = auto()
    YOUTUBE = auto()
    SPOTIFY = auto()
    APPLE = auto()
    MANUAL = auto()


class Episode(Base):
    """A single podcast episode belonging to a podcast source."""

    __tablename__ = "episodes"
    __table_args__ = (
        # Serves both the podcast_id filter and the published_at ordering.
        Index("ix_episodes_podcast_id_published_at", "podcast_id", "published_at"),
        # Supports global episode listings ordered by published_at.
        Index("ix_episodes_published_at", "published_at"),
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
    duration_seconds: Mapped[int | None] = mapped_column(default=None)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    # Source-specific identifiers used for cross-provider deduplication.
    youtube_id: Mapped[str | None] = mapped_column(
        String(20),
        index=True,
        default=None,
    )
    spotify_uri: Mapped[str | None] = mapped_column(
        String(50),
        index=True,
        default=None,
    )
    apple_id: Mapped[str | None] = mapped_column(String(50), default=None)
    rss_guid: Mapped[str | None] = mapped_column(
        String(500),
        index=True,
        default=None,
    )
    episode_url: Mapped[str | None] = mapped_column(String(500), default=None)
    discovery_source: Mapped[DiscoverySource | None] = mapped_column(
        SAEnum(DiscoverySource, native_enum=False, length=50),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    podcast: Mapped["Podcast"] = relationship(back_populates="episodes")
    transcripts: Mapped[list["Transcript"]] = relationship(
        back_populates="episode",
    )
    semantic_chunks: Mapped[list["SemanticChunk"]] = relationship(
        back_populates="episode",
    )
    summaries: Mapped[list["EpisodeSummary"]] = relationship(
        back_populates="episode",
    )
    derivative_runs: Mapped[list["DerivativeGenerationRun"]] = relationship(
        back_populates="episode",
    )
