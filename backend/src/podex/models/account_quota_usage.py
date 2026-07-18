"""Monthly per-account feature quota usage."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AccountQuotaUsage(Base):
    """Consumed quota units for one feature in one UTC billing month."""

    __tablename__ = "account_quota_usage"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "period",
            "feature",
            name="uq_account_quota_usage_user_period_feature",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("account_users.id"), index=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    feature: Mapped[str] = mapped_column(String(40))
    units: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
