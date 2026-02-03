"""Pydantic schemas for episodes."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EpisodeBase(BaseModel):
    """Base episode schema."""

    title: str
    episode_number: int | None = None
    youtube_id: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    thumbnail_url: str | None = None


class EpisodeCreate(EpisodeBase):
    """Schema for creating an episode."""

    podcast_id: int


class EpisodeResponse(EpisodeBase):
    """Schema for episode responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    podcast_id: int
    transcript_status: str
    created_at: datetime


class EpisodeWithStats(EpisodeResponse):
    """Episode response with mention statistics."""

    mention_count: int = 0


class EpisodeBrief(BaseModel):
    """Brief episode info for nested responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    episode_number: int | None = None
    youtube_id: str | None = None
    published_at: datetime | None = None
    thumbnail_url: str | None = None


class EpisodeListResponse(BaseModel):
    """Paginated list of episodes."""

    items: list[EpisodeWithStats]
    total: int
    page: int
    per_page: int
