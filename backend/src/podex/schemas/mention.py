"""Mention API schemas."""

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, computed_field

from podex.api.v2.identifiers import IdentifierKind, encode


class MentionRead(BaseModel):
    """Public representation of a media mention within an episode."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    episode_id: int
    media_id: int
    timestamp_seconds: int | None
    context: str | None
    confidence: float | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def public_id(self) -> str:
        """The opaque, prefixed public identifier for this mention."""
        return cast("str", encode(IdentifierKind.MENTION, self.id))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def episode_public_id(self) -> str:
        """The opaque public identifier of the referenced episode."""
        return cast("str", encode(IdentifierKind.EPISODE, self.episode_id))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def media_public_id(self) -> str:
        """The opaque public identifier of the referenced media item."""
        return cast("str", encode(IdentifierKind.MEDIA, self.media_id))
