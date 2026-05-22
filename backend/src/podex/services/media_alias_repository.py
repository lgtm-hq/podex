"""Database helpers for canonical media aliases."""

from sqlalchemy.orm import Session

from podex.models import Media, MediaAlias, MediaAliasSourceType
from podex.services.media_aliases import (
    CanonicalMediaCandidate,
    CanonicalMediaMatch,
    match_canonical_media,
    normalize_media_alias,
)


def ensure_media_alias(
    *,
    db: Session,
    media: Media,
    alias: str | None,
    source: MediaAliasSourceType = MediaAliasSourceType.REVIEW,
    is_primary: bool = False,
) -> MediaAlias | None:
    """Ensure a normalized alias exists for a media record.

    Args:
        db: Database session.
        media: Canonical media record.
        alias: Alias text to persist.
        source: Source of the alias.
        is_primary: Whether this alias is the primary display title.

    Returns:
        Existing or newly created alias, or ``None`` when alias is empty.
    """
    normalized_alias = normalize_media_alias(alias)
    if alias is None or normalized_alias is None:
        return None

    existing = (
        db.query(MediaAlias)
        .filter(MediaAlias.media_id == media.id)
        .filter(MediaAlias.normalized_alias == normalized_alias)
        .first()
    )
    if existing is not None:
        return existing

    media_alias = MediaAlias(
        media_id=media.id,
        alias=alias.strip(),
        normalized_alias=normalized_alias,
        source=source.value,
        is_primary=is_primary,
    )
    db.add(media_alias)
    db.flush()
    return media_alias


def find_canonical_media_match(
    *,
    db: Session,
    title: str | None,
    media_type: str | None,
) -> CanonicalMediaMatch:
    """Find a canonical media match using persisted aliases.

    Args:
        db: Database session.
        title: Extracted title to resolve.
        media_type: Extracted media type.

    Returns:
        Canonical media match result.
    """
    media_rows = (
        db.query(Media).filter(Media.type == media_type).order_by(Media.id.asc()).all()
    )
    aliases_by_media_id = _load_aliases_by_media_id(
        db=db,
        media_ids=[media.id for media in media_rows],
    )
    candidates = [
        CanonicalMediaCandidate(
            media_id=media.id,
            media_type=media.type,
            title=media.title,
            aliases=tuple(aliases_by_media_id.get(media.id, [])),
        )
        for media in media_rows
    ]
    return match_canonical_media(
        title=title,
        media_type=media_type,
        candidates=candidates,
    )


def _load_aliases_by_media_id(
    *,
    db: Session,
    media_ids: list[int],
) -> dict[int, list[str]]:
    """Load alias strings grouped by media id."""
    if not media_ids:
        return {}

    aliases = (
        db.query(MediaAlias)
        .filter(MediaAlias.media_id.in_(media_ids))
        .order_by(MediaAlias.is_primary.desc(), MediaAlias.id.asc())
        .all()
    )
    grouped: dict[int, list[str]] = {media_id: [] for media_id in media_ids}
    for alias in aliases:
        grouped.setdefault(alias.media_id, []).append(alias.alias)
    return grouped
