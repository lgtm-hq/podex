"""Tests for media alias and external-reference models."""

from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import (
    Media,
    MediaAlias,
    MediaAliasSourceType,
    MediaExternalRef,
)
from tests.conftest import seed_catalog_graph


def test_media_alias_round_trips_with_source(db_session: Session) -> None:
    """Aliases persist with normalization, source, and media linkage."""
    graph = seed_catalog_graph(db_session)

    alias = MediaAlias(
        media_id=graph.media_id,
        alias="Dune (Novel)",
        normalized_alias="dune novel",
        source=MediaAliasSourceType.REVIEW.value,
        is_primary=True,
    )
    db_session.add(alias)
    db_session.commit()

    stored = db_session.execute(select(MediaAlias)).scalar_one()
    assert_that(stored.normalized_alias).is_equal_to("dune novel")
    assert_that(stored.source).is_equal_to(MediaAliasSourceType.REVIEW.value)
    assert_that(stored.media.id).is_equal_to(graph.media_id)
    media = db_session.get(Media, graph.media_id)
    assert_that(media).is_not_none()
    if media is not None:
        assert_that(media.aliases).contains(stored)


def test_media_external_ref_round_trips(db_session: Session) -> None:
    """External refs persist source ids, urls, and metadata."""
    graph = seed_catalog_graph(db_session)

    ref = MediaExternalRef(
        media_id=graph.media_id,
        source="openlibrary",
        external_id="OL893415W",
        url="https://openlibrary.org/works/OL893415W",
        label="Open Library",
        metadata_json={"match": "title"},
    )
    db_session.add(ref)
    db_session.commit()

    stored = db_session.execute(select(MediaExternalRef)).scalar_one()
    assert_that(stored.external_id).is_equal_to("OL893415W")
    assert_that(stored.metadata_json).is_equal_to({"match": "title"})
    assert_that(stored.media.external_refs).contains(stored)
