"""Pydantic schemas for media."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from podex.models.media import MediaType
from podex.schemas.mention import MentionWithEpisode


class MediaBase(BaseModel):
    """Base media schema."""

    type: MediaType
    title: str
    author: str | None = None
    cover_url: str | None = None
    year: int | None = None
    description: str | None = None


class MediaCreate(MediaBase):
    """Schema for creating media."""

    google_books_id: str | None = None
    open_library_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class MediaResponse(MediaBase):
    """Schema for media responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    mention_count: int
    episode_count: int
    created_at: datetime


class MediaDetail(MediaResponse):
    """Detailed media response with mentions and enrichment data."""

    mentions: list[MentionWithEpisode] = []

    # External IDs
    google_books_id: str | None = None
    open_library_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: int | None = None
    wikipedia_id: str | None = None

    # Academic IDs
    doi: str | None = None
    pubmed_id: str | None = None
    semantic_scholar_id: str | None = None

    # Raw metadata
    metadata_json: dict[str, Any] | None = None

    # Enrichment tracking
    enriched_at: datetime | None = None
    enrichment_source: str | None = None

    # Multi-source verification tracking
    verification_sources: list[str] = []
    doi_verified: bool = False

    # Computed fields from metadata_json (populated in API)
    imdb_rating: float | None = None
    tmdb_rating: float | None = None
    rotten_tomatoes: int | None = None
    metacritic: int | None = None
    google_books_rating: float | None = None
    awards: str | None = None
    oscar_wins: int | None = None
    oscar_nominations: int | None = None
    runtime_minutes: int | None = None
    page_count: int | None = None
    genres: list[str] = []
    cast: list[dict[str, Any]] = []
    directors: list[str] = []

    # Academic metadata (computed from metadata_json)
    journal: str | None = None
    authors: list[str] = []
    citation_count: int | None = None
    publication_date: str | None = None
    mesh_terms: list[str] = []
    fields_of_study: list[str] = []
    open_access_pdf_url: str | None = None

    # Movie/TV metadata
    tagline: str | None = None
    budget: int | None = None
    revenue: int | None = None
    production_countries: list[str] = []
    spoken_languages: list[str] = []
    status: str | None = None  # Released, Ended, Returning Series, etc.
    networks: list[str] = []  # For TV shows
    seasons: int | None = None
    episodes: int | None = None

    # Book metadata
    isbn: str | None = None
    publisher: str | None = None
    preview_link: str | None = None
    language: str | None = None

    # Podcast metadata
    podcast_episode_count: int | None = None
    explicit: bool | None = None
    feed_url: str | None = None

    # Person metadata
    biography: str | None = None
    birthday: str | None = None
    birthplace: str | None = None
    known_for: list[str] = []

    # Wikipedia metadata
    wikipedia_categories: list[str] = []

    # External URLs (computed from IDs)
    imdb_url: str | None = None
    tmdb_url: str | None = None
    wikipedia_url: str | None = None
    google_books_url: str | None = None
    open_library_url: str | None = None

    # Academic URLs (computed from IDs)
    doi_url: str | None = None
    pubmed_url: str | None = None
    semantic_scholar_url: str | None = None


class MediaListResponse(BaseModel):
    """Paginated list of media."""

    items: list[MediaResponse]
    total: int
    page: int
    per_page: int
