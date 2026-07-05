"""Media item ORM model."""

from datetime import datetime
from enum import StrEnum, auto

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
