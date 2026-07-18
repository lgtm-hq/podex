"""Triggered account alert event persistence."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountAlertEvent(Base):
    """A generated alert notification awaiting digest/delivery handling."""

    __tablename__ = "account_alert_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("account_alert_rules.id"),
        index=True,
    )
    previous_count: Mapped[int] = mapped_column()
    observed_count: Mapped[int] = mapped_column()
    digest_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_digests.id"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
