"""Delivered account notification digest persistence."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountDigest(Base):
    """A durable record of alert activity delivered to an account."""

    __tablename__ = "account_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account_users.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    subject: Mapped[str] = mapped_column(String(255))
    body_text: Mapped[str] = mapped_column(Text)
    event_count: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    delivered_at: Mapped[datetime | None] = mapped_column()
