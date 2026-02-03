"""Audit log model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import Any

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class AuditAction(StrEnum):
    """Privileged actions that are recorded in the audit log."""

    APPROVE_REVIEW_ITEM = auto()
    ARCHIVE_PODCAST = auto()
    CREATE_PODCAST = auto()
    MERGE_MEDIA = auto()
    MERGE_REVIEW_ITEM = auto()
    RECLASSIFY_REVIEW_ITEM = auto()
    REJECT_REVIEW_ITEM = auto()
    RERUN_EPISODE = auto()
    RUN_PIPELINE = auto()
    UPDATE_PODCAST = auto()
    UPDATE_SETTINGS = auto()


class AuditLog(Base):
    """Immutable audit record for privileged and content-affecting actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), index=True)
    resource_id: Mapped[int | None] = mapped_column(index=True)
    resource_identifier: Mapped[str | None] = mapped_column(String(120))
    actor_name: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
