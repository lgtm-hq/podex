"""Podcast API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PodcastRead(BaseModel):
    """Public representation of a podcast source."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None
    created_at: datetime
