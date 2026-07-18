"""Aggregate/statistics API schemas."""

from pydantic import BaseModel, Field


class MediaTypeCount(BaseModel):
    """One row of the ``top_media_types`` breakdown."""

    media_type: str = Field(description="MediaType value (e.g. ``book``, ``movie``).")
    count: int = Field(description="Number of media rows of this type.", ge=0)


class CatalogStats(BaseModel):
    """Top-level catalog counters plus a small top-list breakdown.

    Attributes:
        podcasts: Total podcast sources in the catalog.
        episodes: Total episodes across all sources.
        media: Total canonical media items referenced by episodes.
        mentions: Total episode<->media mention links.
        top_media_types: Up to five media types ordered by descending count,
            with ties broken alphabetically by type name for stability.
    """

    podcasts: int = Field(ge=0)
    episodes: int = Field(ge=0)
    media: int = Field(ge=0)
    mentions: int = Field(ge=0)
    top_media_types: list[MediaTypeCount]
