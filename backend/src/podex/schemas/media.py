"""Media API schemas."""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, computed_field

from podex.api.v2.identifiers import MEDIA_PREFIX, encode
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_id(self) -> str:
        """The opaque, prefixed public identifier for this media item."""
        return cast("str", encode(MEDIA_PREFIX, self.id))
