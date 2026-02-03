"""Pydantic schemas for podcasts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PodcastBase(BaseModel):
    """Base podcast schema."""

    name: str
    slug: str
    description: str | None = None
    cover_url: str | None = None


class PodcastCreate(PodcastBase):
    """Schema for creating a podcast."""

    pass


class PodcastResponse(PodcastBase):
    """Schema for podcast responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class PodcastWithStats(PodcastResponse):
    """Podcast response with statistics."""

    episode_count: int = 0
    mention_count: int = 0
