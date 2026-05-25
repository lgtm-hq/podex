"""Tests for ops media canonicalization commands."""

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    Media,
    MediaAlias,
    MediaAliasSourceType,
    MediaRelationType,
    Mention,
    Podcast,
)
from podex.services.graph_relations import upsert_media_relation
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


def test_ops_media_detail_and_edit_routes_persist_audited_corrections(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify canonical detail supports audited metadata, alias, and ref edits."""
    media = Media(type="book", title="Working Title", author="Initial Author")
    related = Media(type="person", title="Related Author")
    db_session.add_all([media, related])
    db_session.flush()
    upsert_media_relation(
        db=db_session,
        subject_media_id=related.id,
        object_media_id=media.id,
        relation_type=MediaRelationType.AUTHOR_OF,
        source="operator",
    )
    db_session.commit()

    initial = client.get(f"/api/v2/ops/media/med_{media.id}")
    corrected = client.patch(
        f"/api/v2/ops/media/med_{media.id}",
        json={
            "title": "Canonical Title",
            "description": "Verified description",
            "google_books_id": "gb-canonical",
            "actor_name": "operator",
            "note": "Corrected from publisher listing",
        },
    )
    aliased = client.post(
        f"/api/v2/ops/media/med_{media.id}/aliases",
        json={"alias": "The Alternate Title", "actor_name": "operator"},
    )
    referenced = client.post(
        f"/api/v2/ops/media/med_{media.id}/external-refs",
        json={
            "source": "google_books",
            "external_id": "gb-canonical",
            "url": "https://books.example/gb-canonical",
            "label": "Publisher record",
            "actor_name": "operator",
        },
    )

    assert_that(initial.status_code).is_equal_to(200)
    assert_that(initial.json()["relations"][0]["direction"]).is_equal_to("incoming")
    assert_that(corrected.status_code).is_equal_to(200)
    assert_that(corrected.json()["media"]["title"]).is_equal_to("Canonical Title")
    assert_that(corrected.json()["google_books_id"]).is_equal_to("gb-canonical")
    assert_that(
        [alias["alias"] for alias in corrected.json()["aliases"]],
    ).contains("Working Title", "Canonical Title")
    assert_that(aliased.status_code).is_equal_to(200)
    assert_that([alias["alias"] for alias in aliased.json()["aliases"]]).contains(
        "The Alternate Title",
    )
    assert_that(referenced.status_code).is_equal_to(200)
    assert_that(referenced.json()["external_refs"][0]["external_id"]).is_equal_to(
        "gb-canonical",
    )

    audit = client.get("/api/v2/ops/audit-log?resource_type=media").json()
    assert_that([entry["action"] for entry in audit["items"]]).contains(
        "update_media",
        "add_media_alias",
        "upsert_media_external_ref",
    )


def test_split_ops_media_route_moves_selected_mentions_to_new_record(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify operators can recover selected mentions from an incorrect merge."""
    podcast = Podcast(name="Recovery Podcast", slug="recovery-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Recovery Episode")
    source = Media(type="book", title="Combined Record")
    db_session.add_all([episode, source])
    db_session.flush()
    move = Mention(episode_id=episode.id, media_id=source.id, context="Wrong title")
    remain = Mention(episode_id=episode.id, media_id=source.id, context="Right title")
    db_session.add_all([move, remain])
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/media/med_{source.id}/split",
        json={
            "mention_ids": [f"men_{move.id}"],
            "type": "book",
            "title": "Recovered Record",
            "author": "Correct Author",
            "actor_name": "operator",
            "note": "Recovered from accidental merge",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["mentions_moved"]).is_equal_to(1)
    assert_that(data["source"]["mentions"]).is_length(1)
    assert_that(data["created"]["media"]["title"]).is_equal_to("Recovered Record")
    assert_that(data["created"]["mentions"][0]["id"]).is_equal_to(f"men_{move.id}")
    assert_that(data["created"]["aliases"][0]["alias"]).is_equal_to("Recovered Record")

    audit = client.get("/api/v2/ops/audit-log?resource_type=media").json()
    assert_that(audit["items"][0]["action"]).is_equal_to("split_media")
