"""Pydantic schemas for transcripts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TranscriptBase(BaseModel):
    """Base transcript schema."""

    provider: str
    raw_text: str | None = None
    segments_json: list[dict[str, Any]] | None = None
    fetched_at: datetime | None = None


class TranscriptCreate(TranscriptBase):
    """Schema for creating transcripts."""

    episode_id: int


class TranscriptResponse(TranscriptBase):
    """Schema for transcript responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    created_at: datetime
