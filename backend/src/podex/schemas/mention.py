"""Pydantic schemas for mentions."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from podex.schemas.episode import EpisodeBrief


class MentionBase(BaseModel):
    """Base mention schema."""

    timestamp_seconds: int | None = None
    context: str | None = None
    confidence: float = 1.0


class MentionCreate(MentionBase):
    """Schema for creating a mention."""

    episode_id: int
    media_id: int


class MentionResponse(MentionBase):
    """Schema for mention responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    media_id: int
    created_at: datetime


class MentionWithEpisode(MentionBase):
    """Mention with episode information."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode: EpisodeBrief
    youtube_timestamp_url: str | None = None


class MentionWithMedia(MentionBase):
    """Mention with media information."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    media_id: int
    media_title: str
    media_type: str
    media_author: str | None = None
    youtube_timestamp_url: str | None = None
