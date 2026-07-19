"""Passwordless authentication challenge model."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class MagicLinkToken(Base):
    """One-time hashed token issued for passwordless authentication."""

    __tablename__ = "magic_link_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_users.id"),
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), index=True, default="")
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    redirect_path: Mapped[str | None] = mapped_column(String(300))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
