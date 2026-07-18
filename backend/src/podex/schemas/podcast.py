"""Podcast API schemas."""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, computed_field

from podex.api.v2.identifiers import IdentifierKind, encode


class PodcastRead(BaseModel):
    """Public representation of a podcast source."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    description: str | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_id(self) -> str:
        """The opaque, prefixed public identifier for this podcast."""
        return cast("str", encode(IdentifierKind.PODCAST, self.id))
