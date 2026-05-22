"""Media model."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media_alias import MediaAlias
    from podex.models.mention import Mention


class MediaType(StrEnum):
    """Types of media that can be mentioned."""

    BOOK = "book"
    MOVIE = "movie"
    DOCUMENTARY = "documentary"
    TV_SHOW = "tv_show"
    STUDY = "study"
    PODCAST = "podcast"
    ARTICLE = "article"
    STANDUP_SPECIAL = "standup_special"
    PERSON = "person"
    PLACE = "place"


class Media(Base):
    """Media model representing a book, movie, or other media item."""

    __tablename__ = "media"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    cover_url: Mapped[str | None] = mapped_column(String(500))
    year: Mapped[int | None] = mapped_column()
    description: Mapped[str | None] = mapped_column(Text)

    # External IDs for linking to sources
    google_books_id: Mapped[str | None] = mapped_column(String(50))
    open_library_id: Mapped[str | None] = mapped_column(String(50))
    imdb_id: Mapped[str | None] = mapped_column(String(20))
    tmdb_id: Mapped[int | None] = mapped_column()
    wikipedia_id: Mapped[str | None] = mapped_column(String(100))
    pubmed_id: Mapped[str | None] = mapped_column(String(50))
    doi: Mapped[str | None] = mapped_column(String(100))
    semantic_scholar_id: Mapped[str | None] = mapped_column(String(50))

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    # Multi-source verification tracking
    verification_sources: Mapped[list[str] | None] = mapped_column(JSON)
    doi_verified: Mapped[bool | None] = mapped_column(Boolean, default=False)

    # Enrichment tracking
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrichment_source: Mapped[str | None] = mapped_column(String(50))
    enrichment_confidence: Mapped[float | None] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    mentions: Mapped[list["Mention"]] = relationship(back_populates="media")
    aliases: Mapped[list["MediaAlias"]] = relationship(back_populates="media")

    @property
    def mention_count(self) -> int:
        """Get total number of mentions."""
        return len(self.mentions)

    @property
    def episode_count(self) -> int:
        """Get number of unique episodes where this media was mentioned."""
        return len(set(m.episode_id for m in self.mentions))
