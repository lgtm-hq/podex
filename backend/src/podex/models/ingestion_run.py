"""Ingestion run ORM model."""

from datetime import datetime
from enum import StrEnum, auto

from sqlalchemy import DateTime, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class IngestionRunStatus(StrEnum):
    """Lifecycle state of an ingestion pipeline run."""

    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


class IngestionRun(Base):
    """A single execution of the discovery/ingestion pipeline.

    Rows give ingest jobs an auditable history: when each run started, how
    it ended, and a short error summary when it failed. Discovery and
    recurring-ingest services record one row per run.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[IngestionRunStatus] = mapped_column(
        SAEnum(IngestionRunStatus, native_enum=False, length=32),
        index=True,
    )
    error_summary: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
