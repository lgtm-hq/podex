"""Search projection repair tracking models."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class SearchProjectionRepairResourceType(StrEnum):
    """Resource types that can require projection repair."""

    EPISODE = auto()
    MEDIA = auto()


class SearchProjectionRepairStatus(StrEnum):
    """Lifecycle states for projection repair records."""

    PENDING = auto()
    FAILED = auto()
    COMPLETED = auto()


class SearchProjectionRepairReason(StrEnum):
    """Reasons a projection repair record was opened."""

    EXTRACT_RERUN = auto()
    MEDIA_MERGE = auto()
    REVIEW_PUBLISH = auto()


class SearchProjectionRepair(Base):
    """Track search documents that need to be rebuilt or repaired."""

    __tablename__ = "search_projection_repairs"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[int] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        default=SearchProjectionRepairStatus.PENDING,
    )
    reason: Mapped[str] = mapped_column(String(32), index=True)
    source_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcription_jobs.id"),
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_attempted_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
