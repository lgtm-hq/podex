"""Graph triple model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.media import Media
    from podex.models.media_relation import MediaRelation
    from podex.models.mention import Mention


class GraphTripleObjectKind(StrEnum):
    """Graph triple object storage forms."""

    MEDIA = auto()
    LITERAL = auto()


class GraphTriple(Base):
    """Provenance-backed subject-predicate-object catalog fact."""

    __tablename__ = "graph_triples"
    __table_args__ = (
        UniqueConstraint("triple_key", name="uq_graph_triples_triple_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    triple_key: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    subject_media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    predicate: Mapped[str] = mapped_column(String(80), index=True)
    object_kind: Mapped[str] = mapped_column(String(32), index=True)
    object_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media.id"),
        index=True,
    )
    object_value: Mapped[str | None] = mapped_column(String(500), index=True)
    media_relation_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_relations.id"),
        index=True,
    )
    provenance_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episodes.id"),
        index=True,
    )
    provenance_mention_id: Mapped[int | None] = mapped_column(
        ForeignKey("mentions.id"),
        index=True,
    )
    source: Mapped[str] = mapped_column(String(80), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_text: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    subject_media: Mapped["Media"] = relationship(
        foreign_keys=[subject_media_id],
        back_populates="subject_graph_triples",
    )
    object_media: Mapped["Media | None"] = relationship(
        foreign_keys=[object_media_id],
        back_populates="object_graph_triples",
    )
    media_relation: Mapped["MediaRelation | None"] = relationship()
    provenance_episode: Mapped["Episode | None"] = relationship()
    provenance_mention: Mapped["Mention | None"] = relationship()
