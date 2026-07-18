"""Media item ORM model."""

from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media_alias import MediaAlias
    from podex.models.media_external_ref import MediaExternalRef


class MediaType(StrEnum):
    """The kind of media a catalog item represents."""

    BOOK = auto()
    MOVIE = auto()
    DOCUMENTARY = auto()
    TV_SHOW = auto()
    STUDY = auto()
    PODCAST = auto()
    ARTICLE = auto()
    PERSON = auto()
    PLACE = auto()


class Media(Base):
    """A canonical media item referenced across episodes."""

    __tablename__ = "media"
    __table_args__ = (
        # Supports type-filtered media listings ordered by title.
        Index("ix_media_type_title", "type", "title"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[MediaType] = mapped_column(
        SAEnum(MediaType, native_enum=False, length=50),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), index=True)
    author: Mapped[str | None] = mapped_column(String(255), default=None)
    year: Mapped[int | None] = mapped_column(default=None)
    description: Mapped[str | None] = mapped_column(String(2000), default=None)
    cover_url: Mapped[str | None] = mapped_column(String(1000), default=None)
    # External identifiers and provenance written by enrichment providers.
    google_books_id: Mapped[str | None] = mapped_column(String(50), default=None)
    open_library_id: Mapped[str | None] = mapped_column(String(50), default=None)
    imdb_id: Mapped[str | None] = mapped_column(String(20), default=None)
    tmdb_id: Mapped[int | None] = mapped_column(default=None)
    wikipedia_id: Mapped[str | None] = mapped_column(String(100), default=None)
    pubmed_id: Mapped[str | None] = mapped_column(String(50), default=None)
    doi: Mapped[str | None] = mapped_column(String(100), default=None)
    semantic_scholar_id: Mapped[str | None] = mapped_column(
        String(50),
        default=None,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON,
        default=None,
    )
    verification_sources: Mapped[list[str] | None] = mapped_column(
        JSON,
        default=None,
    )
    doi_verified: Mapped[bool | None] = mapped_column(Boolean, default=False)
    enriched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )
    enrichment_source: Mapped[str | None] = mapped_column(
        String(50),
        default=None,
    )
    enrichment_confidence: Mapped[float | None] = mapped_column(
        Float,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    aliases: Mapped[list["MediaAlias"]] = relationship(back_populates="media")
    external_refs: Mapped[list["MediaExternalRef"]] = relationship(
        back_populates="media",
    )
