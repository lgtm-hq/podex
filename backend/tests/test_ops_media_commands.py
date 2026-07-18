"""Tests for ops media canonicalization commands."""

from assertpy import assert_that
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

    assert_that(preview).is_not_none()
    if preview is None:  # pragma: no cover - narrowed above
        raise AssertionError
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

    assert_that(result).is_not_none()
    if result is None:  # pragma: no cover - narrowed above
        raise AssertionError
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


def test_update_split_alias_and_external_ref_services(
    db_session: Session,
) -> None:
    """Edit, alias, external-ref, and split services operate directly."""
    from podex.models import MediaExternalRefSource, MediaType
    from podex.services.ops_media_commands import (
        SplitOpsMediaInputData,
        UpdateOpsMediaInputData,
        UpsertOpsMediaExternalRefInputData,
        add_ops_media_alias,
        get_ops_media_detail,
        split_ops_media,
        update_ops_media,
        upsert_ops_media_external_ref,
    )

    podcast = Podcast(name="Show", slug="ops-split-show")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Ep")
    media = Media(type=MediaType.BOOK, title="Dune", author="Frank Herbert")
    db_session.add_all([episode, media])
    db_session.commit()
    mentions = [
        Mention(episode_id=episode.id, media_id=media.id, context="one"),
        Mention(episode_id=episode.id, media_id=media.id, context="two"),
    ]
    db_session.add_all(mentions)
    db_session.commit()

    updated = update_ops_media(
        db=db_session,
        media_id=media.id,
        payload=UpdateOpsMediaInputData(
            provided_fields=frozenset({"description", "year"}),
            description="Edited description.",
            year=1965,
        ),
    )
    assert_that(updated).is_not_none()

    alias_detail = add_ops_media_alias(
        db=db_session,
        media_id=media.id,
        alias="Dune (Novel)",
    )
    assert_that(alias_detail).is_not_none()

    ref_detail = upsert_ops_media_external_ref(
        db=db_session,
        media_id=media.id,
        payload=UpsertOpsMediaExternalRefInputData(
            source=MediaExternalRefSource.WIKIPEDIA,
            external_id="Dune_(novel)",
            url="https://en.wikipedia.org/wiki/Dune_(novel)",
        ),
    )
    assert_that(ref_detail).is_not_none()

    split = split_ops_media(
        db=db_session,
        media_id=media.id,
        payload=SplitOpsMediaInputData(
            mention_ids=(mentions[0].id,),
            type=MediaType.MOVIE,
            title="Dune (1984 film)",
        ),
    )
    assert_that(split).is_not_none()
    if split is not None:
        assert_that(split.mentions_moved).is_equal_to(1)

    detail = get_ops_media_detail(db=db_session, media_id=media.id)
    assert_that(detail).is_not_none()
    assert_that(get_ops_media_detail(db=db_session, media_id=99999)).is_none()


def test_media_relation_upserts_and_query_helpers(db_session: Session) -> None:
    """Relation upserts are idempotent; query helpers decode identifiers."""
    from podex.api.query_helpers import mention_count_by_media_subquery
    from podex.models import MediaExternalRefSource, MediaType
    from podex.services.graph_relations import (
        upsert_media_external_ref,
        upsert_media_relation,
    )

    a = Media(type=MediaType.BOOK, title="Dune")
    b = Media(type=MediaType.MOVIE, title="Dune (1984)")
    db_session.add_all([a, b])
    db_session.commit()

    first = upsert_media_relation(
        db=db_session,
        subject_media_id=b.id,
        object_media_id=a.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="review",
        confidence=0.9,
        evidence_text="  adapted from the novel  ",
    )
    second = upsert_media_relation(
        db=db_session,
        subject_media_id=b.id,
        object_media_id=a.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="review",
        confidence=0.95,
    )
    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.confidence).is_equal_to(0.95)

    ref = upsert_media_external_ref(
        db=db_session,
        media=a,
        source=MediaExternalRefSource.WIKIPEDIA,
        external_id="Dune_(novel)",
        url="https://en.wikipedia.org/wiki/Dune_(novel)",
    )
    again = upsert_media_external_ref(
        db=db_session,
        media=a,
        source=MediaExternalRefSource.WIKIPEDIA,
        external_id="Dune_(novel)",
        url="https://en.wikipedia.org/wiki/Dune_(novel)?rev=2",
    )
    assert_that(again.id).is_equal_to(ref.id)

    subquery = mention_count_by_media_subquery(db_session)
    assert_that(subquery).is_not_none()


def test_query_helper_subqueries_and_edit_edge_cases(
    db_session: Session,
) -> None:
    """Remaining query helpers build; edit edge cases behave."""
    from podex.api.query_helpers import (
        episode_count_by_media_subquery,
        episode_count_by_podcast_subquery,
        mention_count_by_episode_subquery,
    )
    from podex.models import MediaType
    from podex.services.ops_media_commands import (
        UpdateOpsMediaInputData,
        add_ops_media_alias,
        update_ops_media,
    )

    assert_that(episode_count_by_media_subquery(db_session)).is_not_none()
    assert_that(episode_count_by_podcast_subquery(db_session)).is_not_none()
    assert_that(mention_count_by_episode_subquery(db_session)).is_not_none()

    media = Media(type=MediaType.BOOK, title="Edge")
    db_session.add(media)
    db_session.commit()

    missing = update_ops_media(
        db=db_session,
        media_id=99999,
        payload=UpdateOpsMediaInputData(),
    )
    assert_that(missing).is_none()
    assert_that(
        add_ops_media_alias(db=db_session, media_id=99999, alias="X"),
    ).is_none()

    try:
        update_ops_media(
            db=db_session,
            media_id=media.id,
            payload=UpdateOpsMediaInputData(
                provided_fields=frozenset({"title"}),
                title="   ",
            ),
        )
        cleared_ok = True
    except ValueError:
        cleared_ok = False
    assert_that(cleared_ok).is_false()

    typed = update_ops_media(
        db=db_session,
        media_id=media.id,
        payload=UpdateOpsMediaInputData(
            provided_fields=frozenset({"type", "imdb_id", "tmdb_id"}),
            type=MediaType.MOVIE,
            imdb_id="tt5555555",
            tmdb_id=99,
        ),
    )
    assert_that(typed).is_not_none()
