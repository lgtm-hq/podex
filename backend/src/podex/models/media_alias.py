"""Media alias model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media import Media


class MediaAliasSourceType(StrEnum):
    """Sources that can create canonical media aliases."""

    REVIEW = auto()
    MERGE = auto()
    ENRICHMENT = auto()
    MANUAL = auto()


class MediaAlias(Base):
    """Alternate title or alias for a canonical media record."""

    __tablename__ = "media_aliases"
    __table_args__ = (
        UniqueConstraint(
            "media_id",
            "normalized_alias",
            name="uq_media_aliases_media_normalized_alias",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    alias: Mapped[str] = mapped_column(String(500))
    normalized_alias: Mapped[str] = mapped_column(String(500), index=True)
    source: Mapped[str] = mapped_column(
        String(32),
        default=MediaAliasSourceType.MANUAL.value,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    media: Mapped["Media"] = relationship(back_populates="aliases")
