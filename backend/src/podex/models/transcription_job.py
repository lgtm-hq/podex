"""Transcription job model for tracking processing status."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode


class JobType(StrEnum):
    """Types of transcription jobs."""

    TRANSCRIBE = auto()
    EXTRACT = auto()
    CLEANUP = auto()


class JobStatus(StrEnum):
    """Status of a transcription job."""

    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


class TranscriptionJob(Base):
    """Model for tracking individual transcription processing jobs."""

    __tablename__ = "transcription_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(32), index=True, default=JobStatus.PENDING
    )
    backend: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))

    episode: Mapped["Episode"] = relationship(back_populates="transcription_jobs")

    def start(self) -> None:
        """Mark job as started."""
        self.status = JobStatus.IN_PROGRESS
        self.started_at = datetime.now(UTC)

    def complete(self, metadata: dict[str, Any] | None = None) -> None:
        """Mark job as completed."""
        self.status = JobStatus.COMPLETED
        self.completed_at = datetime.now(UTC)
        if metadata:
            self.metadata_json = metadata

    def fail(self, error: str) -> None:
        """Mark job as failed."""
        self.status = JobStatus.FAILED
        self.completed_at = datetime.now(UTC)
        self.error_message = error

    def skip(self, reason: str | None = None) -> None:
        """Mark job as skipped."""
        self.status = JobStatus.SKIPPED
        self.completed_at = datetime.now(UTC)
        if reason:
            self.error_message = reason
