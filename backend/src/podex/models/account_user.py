"""Public account identity model."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountUser(Base):
    """Minimal email-backed identity for personalized product features."""

    __tablename__ = "account_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    last_signed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
