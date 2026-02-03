"""Episode model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.mention import Mention
    from podex.models.podcast import Podcast
    from podex.models.transcript import Transcript
    from podex.models.transcription_job import TranscriptionJob


class DiscoverySource(StrEnum):
    """Where an episode or podcast was first discovered."""

    PODSCRIPTS = auto()
    RSS = auto()
    YOUTUBE = auto()
    SPOTIFY = auto()
    APPLE = auto()
    MANUAL = auto()


class Episode(Base):
    """Episode model representing a podcast episode."""

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    podcast_id: Mapped[int] = mapped_column(ForeignKey("podcasts.id"))
    title: Mapped[str] = mapped_column(String(500))
    episode_number: Mapped[int | None] = mapped_column()
    youtube_id: Mapped[str | None] = mapped_column(String(20), index=True)
    published_at: Mapped[datetime | None] = mapped_column()
    duration_seconds: Mapped[int | None] = mapped_column()
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    transcript_status: Mapped[str] = mapped_column(String(20), default="pending")
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")
    cleanup_status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    # External IDs for deduplication
    spotify_uri: Mapped[str | None] = mapped_column(String(50), index=True)
    apple_id: Mapped[str | None] = mapped_column(String(50))
    rss_guid: Mapped[str | None] = mapped_column(String(500), index=True)
    episode_url: Mapped[str | None] = mapped_column(String(500))

    # Where we first discovered this episode
    discovery_source: Mapped[str | None] = mapped_column(String(50))

    podcast: Mapped["Podcast"] = relationship(back_populates="episodes")
    mentions: Mapped[list["Mention"]] = relationship(back_populates="episode")
    transcripts: Mapped[list["Transcript"]] = relationship(back_populates="episode")
    transcription_jobs: Mapped[list["TranscriptionJob"]] = relationship(
        back_populates="episode",
    )
