"""Account alert rule persistence."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountAlertRule(Base):
    """Notification rule over an account-linked public catalog resource."""

    __tablename__ = "account_alert_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            "event_type",
            name="uq_account_alert_rules_user_target_event",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account_users.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(20), index=True)
    target_id: Mapped[int] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    baseline_count: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
