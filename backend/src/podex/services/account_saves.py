"""Authenticated account saved-media operations."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from podex.models import AccountSavedMedia
from podex.schemas import MediaDetail
from podex.services.public_media import get_media_detail_by_id


@dataclass(frozen=True, slots=True)
class SavedMediaData:
    """A saved association and its public catalog representation."""

    media: MediaDetail
    saved_at: datetime


def list_saved_media(*, db: Session, user_id: int) -> list[SavedMediaData]:
    """List a user's saved public media, most recently saved first."""
    rows = (
        db.query(AccountSavedMedia)
        .filter(AccountSavedMedia.user_id == user_id)
        .order_by(AccountSavedMedia.created_at.desc(), AccountSavedMedia.id.desc())
        .all()
    )
    saved: list[SavedMediaData] = []
    for row in rows:
        media = get_media_detail_by_id(db=db, media_id=row.media_id)
        if media is not None:
            saved.append(SavedMediaData(media=media, saved_at=row.created_at))
    return saved


def save_media(*, db: Session, user_id: int, media_id: int) -> SavedMediaData | None:
    """Idempotently save an existing public media record for an account."""
    media = get_media_detail_by_id(db=db, media_id=media_id)
    if media is None:
        return None
    existing = (
        db.query(AccountSavedMedia)
        .filter(
            AccountSavedMedia.user_id == user_id,
            AccountSavedMedia.media_id == media_id,
        )
        .first()
    )
    if existing is not None:
        return SavedMediaData(media=media, saved_at=existing.created_at)
    row = AccountSavedMedia(user_id=user_id, media_id=media_id)
    db.add(row)
    db.flush()
    return SavedMediaData(media=media, saved_at=row.created_at)


def remove_saved_media(*, db: Session, user_id: int, media_id: int) -> bool:
    """Remove a public media record from a user's saves."""
    row = (
        db.query(AccountSavedMedia)
        .filter(
            AccountSavedMedia.user_id == user_id,
            AccountSavedMedia.media_id == media_id,
        )
        .first()
    )
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
