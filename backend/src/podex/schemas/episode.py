"""Episode API schemas."""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, computed_field

from podex.api.v2.identifiers import EPISODE_PREFIX, PODCAST_PREFIX, encode


class EpisodeRead(BaseModel):
    """Public representation of a podcast episode."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    podcast_id: int
    title: str
    episode_number: int | None
    published_at: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_id(self) -> str:
        """The opaque, prefixed public identifier for this episode."""
        return cast("str", encode(EPISODE_PREFIX, self.id))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def podcast_public_id(self) -> str:
        """The opaque public identifier of the owning podcast."""
        return cast("str", encode(PODCAST_PREFIX, self.podcast_id))
