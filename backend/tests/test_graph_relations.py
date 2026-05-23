"""Tests for graph triples and media entity relations."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    GraphTriple,
    GraphTripleObjectKind,
    Media,
    MediaExternalRef,
    MediaExternalRefSource,
    MediaRelation,
    MediaRelationType,
    MediaType,
    Podcast,
)
from podex.services.graph_relations import (
    GraphTripleInputData,
    upsert_graph_triple,
    upsert_media_external_ref,
)


def test_upsert_media_external_ref_deduplicates_source_identifier(
    db_session: Session,
) -> None:
    """Verify external refs are replay-safe per media/source/id."""
    media = Media(type=MediaType.BOOK.value, title="Dune")
    db_session.add(media)
    db_session.flush()

    first = upsert_media_external_ref(
        db=db_session,
        media=media,
        source=MediaExternalRefSource.OPEN_LIBRARY,
        external_id=" OL123W ",
        url="https://openlibrary.org/works/OL123W",
    )
    second = upsert_media_external_ref(
        db=db_session,
        media=media,
        source=MediaExternalRefSource.OPEN_LIBRARY,
        external_id="OL123W",
        label="Open Library",
    )
    db_session.commit()

    assert_that(second.id).is_equal_to(first.id)
    assert_that(second.label).is_equal_to("Open Library")
    assert_that(db_session.query(MediaExternalRef).count()).is_equal_to(1)


def test_upsert_graph_triple_creates_backing_media_relation(
    db_session: Session,
) -> None:
    """Verify media-object triples are backed by typed media relations."""
    subject = Media(type=MediaType.BOOK.value, title="Dune")
    related = Media(type=MediaType.MOVIE.value, title="Dune")
    episode = _create_episode(db_session)
    db_session.add_all([subject, related])
    db_session.flush()
    payload = GraphTripleInputData(
        subject_media_id=subject.id,
        predicate=MediaRelationType.ADAPTED_FROM.value,
        object_media_id=related.id,
        provenance_episode_id=episode.id,
        source="llm-graph-v1",
        confidence=0.92,
        evidence_text="The film was adapted from the novel.",
    )

    first = upsert_graph_triple(db=db_session, payload=payload)
    second = upsert_graph_triple(db=db_session, payload=payload)
    db_session.commit()

    assert_that(second.id).is_equal_to(first.id)
    assert_that(first.object_kind).is_equal_to(GraphTripleObjectKind.MEDIA.value)
    assert_that(first.media_relation_id).is_not_none()
    assert_that(db_session.query(GraphTriple).count()).is_equal_to(1)
    assert_that(db_session.query(MediaRelation).count()).is_equal_to(1)
    relation = db_session.query(MediaRelation).one()
    assert_that(relation.relation_type).is_equal_to(
        MediaRelationType.ADAPTED_FROM.value
    )
    assert_that(relation.provenance_episode_id).is_equal_to(episode.id)


def test_upsert_graph_triple_supports_literal_objects(
    db_session: Session,
) -> None:
    """Verify literal-object triples persist without media relations."""
    media = Media(type=MediaType.PERSON.value, title="Frank Herbert")
    db_session.add(media)
    db_session.flush()
    payload = GraphTripleInputData(
        subject_media_id=media.id,
        predicate="born_in",
        object_value="Tacoma, Washington",
        source="wikipedia",
        confidence=0.99,
    )

    triple = upsert_graph_triple(db=db_session, payload=payload)
    db_session.commit()

    assert_that(triple.object_kind).is_equal_to(GraphTripleObjectKind.LITERAL.value)
    assert_that(triple.object_value).is_equal_to("Tacoma, Washington")
    assert_that(triple.media_relation_id).is_none()
    assert_that(db_session.query(MediaRelation).count()).is_equal_to(0)


def test_upsert_graph_triple_requires_exactly_one_object(
    db_session: Session,
) -> None:
    """Verify graph triples reject missing or ambiguous objects."""
    media = Media(type=MediaType.PERSON.value, title="Frank Herbert")
    related = Media(type=MediaType.PLACE.value, title="Tacoma")
    db_session.add_all([media, related])
    db_session.flush()

    missing_object = GraphTripleInputData(
        subject_media_id=media.id,
        predicate="born_in",
        source="wikipedia",
    )
    ambiguous_object = GraphTripleInputData(
        subject_media_id=media.id,
        predicate=MediaRelationType.ABOUT.value,
        object_media_id=related.id,
        object_value="Tacoma",
        source="wikipedia",
    )

    assert_that(
        lambda: upsert_graph_triple(db=db_session, payload=missing_object),
    ).raises(ValueError).when_called_with()
    assert_that(
        lambda: upsert_graph_triple(db=db_session, payload=ambiguous_object),
    ).raises(ValueError).when_called_with()


def _create_episode(
    db_session: Session,
) -> Episode:
    """Create a persisted JRE episode for graph provenance."""
    podcast = Podcast(name="The Joe Rogan Experience", slug="jre")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(
        podcast_id=podcast.id,
        title="JRE #1",
        episode_number=1,
    )
    db_session.add(episode)
    db_session.flush()
    return episode
