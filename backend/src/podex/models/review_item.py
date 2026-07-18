"""Review item model."""

from datetime import UTC, datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from podex.models.base import Base

if TYPE_CHECKING:
    from podex.models.media import Media
    from podex.models.mention_candidate import MentionCandidate


class ReviewItemStatus(StrEnum):
    """Review queue statuses for operator decisions."""

    PENDING = auto()
    IN_REVIEW = auto()
    APPROVED = auto()
    REJECTED = auto()
    MERGED = auto()
    SPLIT = auto()


class ReviewPriority(StrEnum):
    """Priority assigned to a review queue item."""

    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class ReviewItem(Base):
    """Operator-facing review item for a single mention candidate."""

    __tablename__ = "review_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    mention_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("mention_candidates.id"),
        unique=True,
        index=True,
    )
    target_media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media.id"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        index=True,
        default=ReviewItemStatus.PENDING.value,
    )
    priority: Mapped[str] = mapped_column(
        String(16),
        index=True,
        default=ReviewPriority.MEDIUM.value,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    decided_at: Mapped[datetime | None] = mapped_column()

    mention_candidate: Mapped["MentionCandidate"] = relationship(
        back_populates="review_item",
    )
    target_media: Mapped["Media | None"] = relationship()

    def assign(self, *, actor_name: str | None = None) -> None:
        """Move the item into active review.

        Args:
            actor_name: Optional operator name to attach to the item.
        """
        self.status = ReviewItemStatus.IN_REVIEW.value
        self.assigned_to = actor_name or self.assigned_to
        self.updated_at = datetime.now(UTC)

    def approve(
        self,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> None:
        """Mark the item as approved.

        Args:
            actor_name: Optional operator name to attach to the item.
            note: Optional decision note.
        """
        now = datetime.now(UTC)
        self.status = ReviewItemStatus.APPROVED.value
        self.assigned_to = actor_name or self.assigned_to
        self.decision_note = note
        self.updated_at = now
        self.decided_at = now

    def reject(
        self,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> None:
        """Mark the item as rejected.

        Args:
            actor_name: Optional operator name to attach to the item.
            note: Optional decision note.
        """
        now = datetime.now(UTC)
        self.status = ReviewItemStatus.REJECTED.value
        self.assigned_to = actor_name or self.assigned_to
        self.decision_note = note
        self.updated_at = now
        self.decided_at = now

    def mark_merged(
        self,
        *,
        actor_name: str | None = None,
        note: str | None = None,
        target_media_id: int,
    ) -> None:
        """Mark the item as merged into an existing canonical media record.

        Args:
            actor_name: Optional operator name to attach to the item.
            note: Optional decision note.
            target_media_id: Internal media identifier merged into.
        """
        now = datetime.now(UTC)
        self.status = ReviewItemStatus.MERGED.value
        self.assigned_to = actor_name or self.assigned_to
        self.decision_note = note
        self.target_media_id = target_media_id
        self.updated_at = now
        self.decided_at = now

    def mark_split(
        self,
        *,
        actor_name: str | None = None,
        note: str | None = None,
    ) -> None:
        """Mark the item as split into replacement review candidates."""
        now = datetime.now(UTC)
        self.status = ReviewItemStatus.SPLIT.value
        self.assigned_to = actor_name or self.assigned_to
        self.decision_note = note
        self.updated_at = now
        self.decided_at = now
