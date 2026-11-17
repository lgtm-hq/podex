"""Lock media records before touching rows that reference them (ADR 0020).

Every writer of rows that point at a media record (mentions, saves, alert
rules and their events, pair suppressions, media merges and splits) takes the
parent media rows first, in ascending id, across its whole set, before it
reads eligibility or counts and before it writes. ``lock_media`` is that
first step: it locks through ``lock_media_rows``, the same lock the flush
guard takes, and then re-reads the records, so the caller sees what a merge
or split that committed while it waited left behind.
"""

from collections.abc import Iterable

from sqlalchemy.orm import Session

from podex.models import Media
from podex.models.content_suppression import batches
from podex.models.suppression_guard import lock_media_rows


def lock_media(
    *,
    db: Session,
    media_ids: Iterable[int],
    exclusive: bool = False,
) -> dict[int, Media]:
    """Lock media rows in ascending id order, then re-read them.

    ``FOR KEY SHARE`` by default, for writers of rows that reference the
    records; ``exclusive`` (``FOR UPDATE``) for merges and splits, which
    change what the records are. Call it with the whole set, before any other
    lock and before changing these records in this session: the re-read
    replaces their loaded state. SQLite ignores the lock clause.

    Args:
        db: Session whose transaction takes the locks.
        media_ids: Records the caller is about to read or write around.
        exclusive: Lock ``FOR UPDATE`` instead of ``FOR KEY SHARE``.

    Returns:
        The records that still exist, by id.
    """
    wanted = sorted(set(media_ids))
    lock_media_rows(session=db, media_ids=wanted, exclusive=exclusive)
    found: dict[int, Media] = {}
    for batch in batches(wanted):
        for media in (
            db.query(Media).filter(Media.id.in_(batch)).populate_existing().all()
        ):
            found[media.id] = media
    return found
