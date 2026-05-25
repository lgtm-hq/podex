"""Saved public catalog media for an account."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountSavedMedia(Base):
    """A user's saved reference to a canonical public media record."""

    __tablename__ = "account_saved_media"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "media_id",
            name="uq_account_saved_media_user_media",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account_users.id"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
