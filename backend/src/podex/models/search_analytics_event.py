"""Anonymous public search relevance signal model."""

from datetime import UTC, datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class SearchAnalyticsEvent(Base):
    """Record a query or selected result without account identity."""

    __tablename__ = "search_analytics_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)
    query: Mapped[str] = mapped_column(String(200), index=True)
    result_count: Mapped[int | None] = mapped_column()
    processing_time_ms: Mapped[int | None] = mapped_column()
    selected_type: Mapped[str | None] = mapped_column(String(20), index=True)
    selected_id: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        index=True,
    )
