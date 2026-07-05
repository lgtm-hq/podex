"""Media API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from podex.models.media import MediaType


class MediaRead(BaseModel):
    """Public representation of a canonical media item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: MediaType
    title: str
    author: str | None
    year: int | None
    description: str | None
    cover_url: str | None
    created_at: datetime
