"""Durable traversal position for recurring batched sweeps."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from podex.models.base import Base


class SweepCheckpoint(Base):
    """Keyset position of one recurring sweep within its current cycle.

    A cycle visits every row whose ID is at most ``cycle_upper_id``, the
    largest ID present when the cycle started. ``last_id`` is the largest ID
    the sweep has committed in that cycle. Both are empty between cycles, so
    the next batch starts a fresh cycle from the lowest ID.
    """

    __tablename__ = "sweep_checkpoints"

    id: "Mapped[int]" = mapped_column(primary_key=True)
    sweep_key: "Mapped[str]" = mapped_column(String(160), unique=True, index=True)
    last_id: "Mapped[int | None]" = mapped_column(Integer)
    cycle_upper_id: "Mapped[int | None]" = mapped_column(Integer)
    updated_at: "Mapped[datetime]" = mapped_column(DateTime(timezone=True))
