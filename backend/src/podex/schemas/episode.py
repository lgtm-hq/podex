"""Episode API schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EpisodeRead(BaseModel):
    """Public representation of a podcast episode."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    podcast_id: int
    title: str
    episode_number: int | None
    published_at: datetime | None
    created_at: datetime
