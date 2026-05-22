"""Tests for persisted canonical media aliases."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Media, MediaAlias, MediaAliasSourceType, MediaType
from podex.services.media_alias_repository import (
    ensure_media_alias,
    find_canonical_media_match,
)


def test_ensure_media_alias_deduplicates_normalized_aliases(
    db_session: Session,
) -> None:
    """Verify alias persistence is replay safe for normalized values."""
    media = Media(type=MediaType.MOVIE.value, title="Star Wars")
    db_session.add(media)
    db_session.flush()

    first = ensure_media_alias(
        db=db_session,
        media=media,
        alias="A New Hope",
        source=MediaAliasSourceType.REVIEW,
    )
    second = ensure_media_alias(
        db=db_session,
        media=media,
        alias="a new hope",
        source=MediaAliasSourceType.REVIEW,
    )
    db_session.commit()

    assert_that(first).is_not_none()
    assert_that(second).is_equal_to(first)
    assert_that(db_session.query(MediaAlias).count()).is_equal_to(1)


def test_find_canonical_media_match_uses_persisted_aliases(
    db_session: Session,
) -> None:
    """Verify persisted aliases participate in canonical matching."""
    media = Media(type=MediaType.MOVIE.value, title="Star Wars")
    db_session.add(media)
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=media,
        alias="A New Hope",
        source=MediaAliasSourceType.REVIEW,
    )
    db_session.commit()

    result = find_canonical_media_match(
        db=db_session,
        title="a new hope",
        media_type=MediaType.MOVIE.value,
    )

    assert_that(result.media_id).is_equal_to(media.id)
    assert_that(result.reason.value).is_equal_to("exact_alias")
