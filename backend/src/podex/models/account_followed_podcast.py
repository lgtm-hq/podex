"""Followed public podcast sources for an account."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountFollowedPodcast(Base):
    """A user's followed public podcast source."""

    __tablename__ = "account_followed_podcasts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "podcast_id",
            name="uq_account_followed_podcasts_user_podcast",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account_users.id"), index=True)
    podcast_id: Mapped[int] = mapped_column(ForeignKey("podcasts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
