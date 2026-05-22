"""Tests for ops media canonicalization commands."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Media,
    MediaAlias,
    MediaAliasSourceType,
    Mention,
    Podcast,
)
from podex.services.media_alias_repository import ensure_media_alias
from podex.services.ops_media_commands import merge_ops_media, preview_ops_media_merge


def test_preview_ops_media_merge_reports_fields_aliases_and_mentions(
    db_session: Session,
) -> None:
    """Verify merge preview reports non-mutating canonicalization effects."""
    podcast = Podcast(name="Preview Podcast", slug="preview-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Preview Episode")
    source = Media(type="book", title="Source Title", author="Source Author")
    target = Media(type="book", title="Target Title")
    db_session.add_all([episode, source, target])
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=source,
        alias="Alternate Source",
        source=MediaAliasSourceType.MANUAL,
    )
    db_session.add(Mention(episode_id=episode.id, media_id=source.id))
    db_session.flush()

    preview = preview_ops_media_merge(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )

    assert preview is not None
    assert_that(preview.mentions_to_move).is_equal_to(1)
    assert_that([change.field for change in preview.field_changes]).contains("author")
    assert_that([alias.alias for alias in preview.alias_additions]).contains(
        "Source Title",
        "Alternate Source",
    )
    assert_that(
        db_session.query(Media).filter(Media.id == source.id).count(),
    ).is_equal_to(1)


def test_merge_ops_media_moves_source_aliases_to_target(
    db_session: Session,
) -> None:
    """Verify canonical merges preserve source aliases on the target."""
    source = Media(type="book", title="Source Title")
    target = Media(type="book", title="Target Title")
    db_session.add_all([source, target])
    db_session.flush()
    ensure_media_alias(
        db=db_session,
        media=source,
        alias="Alternate Source",
        source=MediaAliasSourceType.MANUAL,
    )
    db_session.commit()

    result = merge_ops_media(
        db=db_session,
        source_media_id=source.id,
        target_media_id=target.id,
    )
    db_session.commit()

    assert result is not None
    aliases = (
        db_session.query(MediaAlias)
        .filter(MediaAlias.media_id == target.id)
        .order_by(MediaAlias.alias.asc())
        .all()
    )
    assert_that([alias.alias for alias in aliases]).contains(
        "Alternate Source",
        "Source Title",
        "Target Title",
    )
    assert_that(db_session.query(Media).filter(Media.id == source.id).count()).is_zero()


def test_preview_ops_media_merge_v2_endpoint(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify v2 ops merge preview exposes alias and field changes."""
    source = Media(type="book", title="Source Title", author="Source Author")
    target = Media(type="book", title="Target Title")
    db_session.add_all([source, target])
    db_session.commit()

    response = client.get(
        f"/api/v2/ops/media/med_{source.id}/merge-preview?target_id=med_{target.id}",
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["source"]["id"]).is_equal_to(f"med_{source.id}")
    assert_that(data["target"]["id"]).is_equal_to(f"med_{target.id}")
    assert_that(data["field_changes"][0]["field"]).is_equal_to("author")
    assert_that(data["alias_additions"][0]["alias"]).is_equal_to("Source Title")
