"""Episode ORM model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class Episode(Base):
    """A single podcast episode belonging to a podcast source."""

    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    podcast_id: Mapped[int] = mapped_column(
        ForeignKey("podcasts.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), index=True)
    episode_number: Mapped[int | None] = mapped_column(default=None)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
