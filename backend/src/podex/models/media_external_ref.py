"""Media external reference model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media import Media


class MediaExternalRefSource(StrEnum):
    """Supported external reference sources for canonical media."""

    DOI = auto()
    GOOGLE_BOOKS = auto()
    IMDB = auto()
    MANUAL = auto()
    OPEN_LIBRARY = auto()
    PUBMED = auto()
    SEMANTIC_SCHOLAR = auto()
    TMDB = auto()
    WIKIPEDIA = auto()


class MediaExternalRef(Base):
    """Canonical external identifier or URL for a media record."""

    __tablename__ = "media_external_refs"
    __table_args__ = (
        UniqueConstraint(
            "media_id",
            "source",
            "external_id",
            name="uq_media_external_refs_media_source_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    url: Mapped[str | None] = mapped_column(String(500))
    label: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    media: Mapped["Media"] = relationship(back_populates="external_refs")
