"""Rights-holder and creator takedown request persistence."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class TakedownSubjectType(StrEnum):
    """Public catalog resources that may receive takedown requests."""

    PODCAST = auto()
    EPISODE = auto()
    MENTION = auto()


class TakedownRequesterType(StrEnum):
    """Claimant relationships accepted for a takedown submission."""

    CREATOR = auto()
    RIGHTS_HOLDER = auto()
    OPERATOR = auto()


class TakedownRequestStatus(StrEnum):
    """Review states for takedown requests."""

    PENDING = auto()
    APPROVED = auto()
    REJECTED = auto()


class TakedownRequest(Base):
    """Privileged case record for a request to suppress catalog content."""

    __tablename__ = "takedown_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    requester_type: Mapped[str] = mapped_column(String(32), index=True)
    requester_name: Mapped[str] = mapped_column(String(255))
    requester_email: Mapped[str] = mapped_column(String(320))
    basis: Mapped[str] = mapped_column(Text)
    requested_actions_json: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        default=TakedownRequestStatus.PENDING.value,
    )
    decision_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
