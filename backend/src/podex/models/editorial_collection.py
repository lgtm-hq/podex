"""Curated public editorial collection models."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class EditorialCollection(Base):
    """Published curated group of public catalog references."""

    __tablename__ = "editorial_collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    curator_name: Mapped[str | None] = mapped_column(String(120))
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class EditorialCollectionItem(Base):
    """Ordered public media placement inside an editorial collection."""

    __tablename__ = "editorial_collection_items"
    __table_args__ = (
        UniqueConstraint(
            "collection_id",
            "media_id",
            name="uq_editorial_collection_items_collection_media",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("editorial_collections.id"),
        index=True,
    )
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    position: Mapped[int] = mapped_column(default=0)
    note: Mapped[str | None] = mapped_column(Text)
