"""Mention API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MentionRead(BaseModel):
    """Public representation of a media mention within an episode."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    media_id: int
    timestamp_seconds: int | None
    context: str | None
    confidence: float | None
    created_at: datetime
