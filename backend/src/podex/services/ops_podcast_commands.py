"""Shared podcast management mutation services for ops surfaces."""

import sqlite3
from dataclasses import dataclass

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import DiscoverySource, Podcast, PodcastStatus
from podex.services.ops_podcast_queries import (
    OpsPodcastSummaryData,
    get_ops_podcast_by_id,
)

SLUG_UNIQUE_INDEX = "ix_podcasts_slug"
"""Unique index on ``podcasts.slug``, named by migration 0001."""

_SQLITE_SLUG_UNIQUE_MESSAGE = "UNIQUE constraint failed: podcasts.slug"
"""SQLite's message for a duplicate value in the slug's unique index."""

DISCOVERY_SOURCE_ECHO_LIMIT = 50
"""Most characters of a rejected discovery source echoed in its message.

Equal to the column length, so every value a request may submit is echoed in
full, while a longer value passed to the service directly cannot grow the
message.
"""


class OpsPodcastValidationError(ValueError):
    """A podcast mutation was rejected on purpose by its validation rules.

    The message is written for the operator and is safe to return in a
    response. Any other ``ValueError`` raised while creating or updating a
    podcast is an internal failure, not a validation result.
    """


@dataclass(frozen=True, slots=True)
class OpsPodcastSourceInputData:
    """Source fields provided for ops podcast mutations."""

    # Explicit mapped/input fields preserve this independent table or boundary contract.
    # pylint: disable=duplicate-code
    rss_url: str | None = None
    spotify_id: str | None = None
    apple_id: str | None = None
    youtube_channel_id: str | None = None
    podscripts_slug: str | None = None
    # pylint: enable=duplicate-code


@dataclass(frozen=True, slots=True)
class CreateOpsPodcastInputData:
    """Required and optional fields for podcast creation."""

    name: str
    slug: str
    status: PodcastStatus
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInputData = OpsPodcastSourceInputData()


@dataclass(frozen=True, slots=True)
# This flat data record mirrors its published fields; nesting changes the contract.
# pylint: disable-next=too-many-instance-attributes
class UpdateOpsPodcastInputData:
    """Partial fields accepted for podcast updates."""

    provided_fields: frozenset[str] = frozenset()
    source_fields: frozenset[str] = frozenset()
    name: str | None = None
    slug: str | None = None
    status: PodcastStatus | None = None
    description: str | None = None
    cover_url: str | None = None
    discovery_source: str | None = None
    sources: OpsPodcastSourceInputData | None = None


def create_ops_podcast(
    *,
    db: Session,
    payload: CreateOpsPodcastInputData,
) -> OpsPodcastSummaryData:
    """Create a podcast for ops catalog management.

    Args:
        db: Database session.
        payload: Podcast creation payload.

    Returns:
        Created podcast summary.

    Raises:
        OpsPodcastValidationError: If the discovery source is not a known
            value; nothing is added to the session.
        RuntimeError: If the created podcast cannot be reloaded.
    """
    podcast = Podcast(
        name=payload.name,
        slug=payload.slug,
        status=payload.status.value,
        description=payload.description,
        cover_url=payload.cover_url,
        discovery_source=_parse_discovery_source(payload.discovery_source),
        rss_url=payload.sources.rss_url,
        spotify_id=payload.sources.spotify_id,
        apple_id=payload.sources.apple_id,
        youtube_channel_id=payload.sources.youtube_channel_id,
        podscripts_slug=payload.sources.podscripts_slug,
    )
    db.add(podcast)
    db.flush()

    created_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if created_podcast is None:
        raise RuntimeError("Created podcast could not be reloaded")
    return created_podcast


def _parse_discovery_source(value: str | None) -> DiscoverySource | None:
    """Convert a provided discovery source to its enum member.

    Raises:
        OpsPodcastValidationError: If the value is not a known discovery
            source. The message echoes at most ``DISCOVERY_SOURCE_ECHO_LIMIT``
            characters of the value.
    """
    if value is None:
        return None
    try:
        return DiscoverySource(value)
    except ValueError as error:
        shown = repr(value[:DISCOVERY_SOURCE_ECHO_LIMIT])
        if len(value) > DISCOVERY_SOURCE_ECHO_LIMIT:
            shown = f"{shown}..."
        raise OpsPodcastValidationError(
            f"{shown} is not a valid DiscoverySource",
        ) from error


def _validate_update(payload: UpdateOpsPodcastInputData) -> DiscoverySource | None:
    """Validate every provided field of a partial update before any is applied.

    Args:
        payload: Partial update payload.

    Returns:
        The parsed discovery source, or ``None`` when absent or cleared.

    Raises:
        OpsPodcastValidationError: If a required field is cleared or the
            discovery source is not a known value.
    """
    for field in ("name", "slug", "status"):
        if field in payload.provided_fields and getattr(payload, field) is None:
            raise OpsPodcastValidationError(f"{field} cannot be cleared")
    if "discovery_source" not in payload.provided_fields:
        return None
    return _parse_discovery_source(payload.discovery_source)


def _apply_profile_fields(
    *,
    podcast: Podcast,
    payload: UpdateOpsPodcastInputData,
    discovery_source: DiscoverySource | None,
) -> None:
    """Assign the provided, already validated profile fields to the podcast."""
    provided = payload.provided_fields
    if "name" in provided and payload.name is not None:
        podcast.name = payload.name
    if "slug" in provided and payload.slug is not None:
        podcast.slug = payload.slug
    if "status" in provided and payload.status is not None:
        podcast.status = payload.status.value
    if "description" in provided:
        podcast.description = payload.description
    if "cover_url" in provided:
        podcast.cover_url = payload.cover_url
    if "discovery_source" in provided:
        podcast.discovery_source = discovery_source


def _apply_source_fields(
    *, podcast: Podcast, payload: UpdateOpsPodcastInputData
) -> None:
    """Assign the source identifiers named in ``source_fields`` to the podcast."""
    if payload.sources is None:
        return
    if "rss_url" in payload.source_fields:
        podcast.rss_url = payload.sources.rss_url
    if "spotify_id" in payload.source_fields:
        podcast.spotify_id = payload.sources.spotify_id
    if "apple_id" in payload.source_fields:
        podcast.apple_id = payload.sources.apple_id
    if "youtube_channel_id" in payload.source_fields:
        podcast.youtube_channel_id = payload.sources.youtube_channel_id
    if "podscripts_slug" in payload.source_fields:
        podcast.podscripts_slug = payload.sources.podscripts_slug


def update_ops_podcast(
    *,
    db: Session,
    podcast_id: int,
    payload: UpdateOpsPodcastInputData,
) -> OpsPodcastSummaryData | None:
    """Update a podcast for ops catalog management.

    Args:
        db: Database session.
        podcast_id: Internal podcast identifier.
        payload: Partial update payload.

    Returns:
        Updated podcast summary when found, otherwise ``None``.

    Raises:
        OpsPodcastValidationError: If a provided field is invalid; the loaded
            podcast is left unmodified.
        sqlalchemy.exc.IntegrityError: If the flush violates a constraint, for
            example a slug that another podcast already uses.
        RuntimeError: If the updated podcast cannot be reloaded.
    """
    podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
    if podcast is None:
        return None

    discovery_source = _validate_update(payload)
    _apply_profile_fields(
        podcast=podcast,
        payload=payload,
        discovery_source=discovery_source,
    )
    _apply_source_fields(podcast=podcast, payload=payload)

    db.flush()

    updated_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if updated_podcast is None:
        raise RuntimeError("Updated podcast could not be reloaded")
    return updated_podcast


def is_slug_conflict(error: IntegrityError) -> bool:
    """Return whether ``error`` is a duplicate slug and nothing else.

    The decision uses only the driver error the failed statement raised, so
    it does not depend on rows that may change after the failure:

    - PostgreSQL (psycopg): a ``UniqueViolation`` (SQLSTATE ``23505``) whose
      constraint is ``SLUG_UNIQUE_INDEX``.
    - SQLite: an ``IntegrityError`` with extended code
      ``SQLITE_CONSTRAINT_UNIQUE`` whose message names ``podcasts.slug`` alone.

    Anything else, including another unique index, a check, trigger, foreign
    key or NOT NULL failure, or an unknown driver, is not a slug conflict.

    Args:
        error: Integrity error raised by a podcast flush.

    Returns:
        ``True`` only for a positively identified slug unique violation.
    """
    driver_error = error.orig
    if isinstance(driver_error, UniqueViolation):
        return driver_error.diag.constraint_name == SLUG_UNIQUE_INDEX
    if isinstance(driver_error, sqlite3.IntegrityError):
        return (
            getattr(driver_error, "sqlite_errorname", None)
            == "SQLITE_CONSTRAINT_UNIQUE"
            and str(driver_error) == _SQLITE_SLUG_UNIQUE_MESSAGE
        )
    return False


def archive_ops_podcast(
    *,
    db: Session,
    podcast_id: int,
) -> OpsPodcastSummaryData | None:
    """Archive a podcast for ops catalog management.

    Args:
        db: Database session.
        podcast_id: Internal podcast identifier.

    Returns:
        Archived podcast summary when found, otherwise ``None``.

    Raises:
        RuntimeError: If the archived podcast cannot be reloaded.
    """
    podcast = db.query(Podcast).filter(Podcast.id == podcast_id).first()
    if podcast is None:
        return None

    podcast.status = PodcastStatus.PAUSED.value
    db.flush()

    archived_podcast = get_ops_podcast_by_id(db=db, podcast_id=podcast.id)
    if archived_podcast is None:
        raise RuntimeError("Archived podcast could not be reloaded")
    return archived_podcast
