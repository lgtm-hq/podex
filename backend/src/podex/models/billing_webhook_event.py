"""Processed billing webhook event ledger for idempotent delivery."""

from datetime import UTC, datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class BillingWebhookEvent(Base):
    """One processed provider webhook event, keyed for replay suppression."""

    __tablename__ = "billing_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "event_id",
            name="uq_billing_webhook_events_provider_event",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    event_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    processed_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
    )
