"""Media relation model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.media import Media


class MediaRelationType(StrEnum):
    """Supported canonical media-to-media relation types."""

    ADAPTED_FROM = auto()
    ABOUT = auto()
    AUTHOR_OF = auto()
    REFERENCES = auto()
    SIMILAR_TO = auto()
    MENTIONED_WITH = auto()


class MediaRelation(Base):
    """Typed edge connecting two canonical media records."""

    __tablename__ = "media_relations"
    __table_args__ = (
        UniqueConstraint("relation_key", name="uq_media_relations_relation_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    relation_key: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    subject_media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    object_media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    provenance_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id"),
        index=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    subject_media: Mapped["Media"] = relationship(
        foreign_keys=[subject_media_id],
        back_populates="outgoing_relations",
    )
    object_media: Mapped["Media"] = relationship(
        foreign_keys=[object_media_id],
        back_populates="incoming_relations",
    )
    provenance_episode: Mapped["Episode | None"] = relationship()
