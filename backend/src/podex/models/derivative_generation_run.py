"""Derivative generation run model."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.episode import Episode
    from podex.models.episode_summary import EpisodeSummary
    from podex.models.transcript import Transcript


class DerivativeGenerationRunStatus(StrEnum):
    """Lifecycle states for derivative generation runs."""

    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()


class DerivativeGenerationRun(Base):
    """Replay-safe derivative generation execution record."""

    __tablename__ = "derivative_generation_runs"
    __table_args__ = (
        UniqueConstraint(
            "run_key",
            name="uq_derivative_generation_runs_run_key",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    transcript_id: Mapped[int | None] = mapped_column(
        ForeignKey("transcripts.id"),
        index=True,
    )
    episode_summary_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode_summaries.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default=DerivativeGenerationRunStatus.RUNNING.value,
        index=True,
    )
    pipeline_version: Mapped[str] = mapped_column(String(80), index=True)
    chunk_pipeline_version: Mapped[str] = mapped_column(String(80), index=True)
    summary_prompt_version: Mapped[str] = mapped_column(String(80), index=True)
    summary_model: Mapped[str | None] = mapped_column(String(120), index=True)
    source_text_hash: Mapped[str] = mapped_column(String(64), index=True)
    semantic_chunks_created: Mapped[int] = mapped_column(Integer, default=0)
    semantic_chunks_updated: Mapped[int] = mapped_column(Integer, default=0)
    semantic_chunks_deleted: Mapped[int] = mapped_column(Integer, default=0)
    semantic_chunks_embedded: Mapped[int] = mapped_column(Integer, default=0)
    semantic_chunks_failed: Mapped[int] = mapped_column(Integer, default=0)
    media_summaries_generated: Mapped[int] = mapped_column(Integer, default=0)
    graph_triples_upserted: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    episode: Mapped[Episode] = relationship(back_populates="derivative_runs")
    transcript: Mapped[Transcript | None] = relationship(
        back_populates="derivative_runs",
    )
    episode_summary: Mapped[EpisodeSummary | None] = relationship()
