"""Moves out of suppressed content, remembered checks and summary-drop scope.

Each test pins one protection that a broader test would not catch alone:
moving hidden or derived content out of a suppressed episode, forgetting
remembered checks when suppression rows change in bulk or a savepoint ends,
and which media summaries a takedown drops.
"""

from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    Episode,
    EpisodeSummary,
    GraphTriple,
    Media,
    MediaSummary,
    MediaType,
    Mention,
    Podcast,
    TakedownRequest,
    TakedownSubjectType,
)
from podex.services.graph_relations import GraphTripleInputData, upsert_graph_triple
from tests.conftest import seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    summary_values,
    take_down_episode,
    take_down_mention,
)

_AT = datetime(2026, 7, 1, tzinfo=UTC)


def _open_episode(db: Session, podcast_id: int, title: str = "Open") -> int:
    episode = Episode(podcast_id=podcast_id, title=title)
    db.add(episode)
    db.commit()
    episode_id: int = episode.id
    return episode_id


def test_hidden_mention_cannot_move_to_another_episode(db_session: Session) -> None:
    """A mention restored into a suppressed pair cannot leave it by episode."""
    graph = seed_catalog_graph(db_session)
    open_episode = _open_episode(db_session, graph.podcast_id)
    take_down_mention(db_session, graph.mention_id)
    db_session.execute(
        insert(Mention).values(episode_id=graph.episode_id, media_id=graph.media_id),
    )
    db_session.commit()
    hidden = db_session.query(Mention).one()

    hidden.episode_id = open_episode
    with pytest.raises(
        ContentSuppressedError, match="moved out of its suppressed pair"
    ):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("column", ["episode_id", "media_id"])
def test_expired_hidden_mention_cannot_move_out_of_its_pair(
    db_session: Session,
    column: str,
) -> None:
    """An expired row's old ids are loaded, so the origin is still checked.

    After a commit every loaded row is expired. Assigning a column then
    records no old value unless the mapping asks for it, and the origin check
    would compare the destination with itself.
    """
    graph = seed_catalog_graph(db_session)
    open_episode = _open_episode(db_session, graph.podcast_id)
    other_media = Media(type=MediaType.BOOK, title="Open work")
    db_session.add(other_media)
    db_session.commit()
    destination = {"episode_id": open_episode, "media_id": other_media.id}[column]
    take_down_mention(db_session, graph.mention_id)
    db_session.execute(
        insert(Mention).values(episode_id=graph.episode_id, media_id=graph.media_id),
    )
    db_session.commit()
    hidden = db_session.query(Mention).one()
    db_session.expire(hidden)

    setattr(hidden, column, destination)
    with pytest.raises(
        ContentSuppressedError, match="moved out of its suppressed pair"
    ):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("expired", [False, True], ids=["loaded", "expired"])
def test_derivative_row_cannot_move_out_of_a_suppressed_episode(
    db_session: Session,
    expired: bool,
) -> None:
    """An episode summary of a suppressed episode cannot be re-attributed."""
    graph = seed_catalog_graph(db_session)
    open_episode = _open_episode(db_session, graph.podcast_id)
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    db_session.execute(
        insert(EpisodeSummary).values(
            **summary_values(graph.episode_id, "restored", "suppressed summary"),
            status="ready",
            created_at=_AT,
            updated_at=_AT,
        ),
    )
    db_session.commit()
    summary = db_session.query(EpisodeSummary).one()
    if expired:
        db_session.expire(summary)

    summary.episode_id = open_episode
    with pytest.raises(ContentSuppressedError, match="moved out of the episode"):
        db_session.commit()
    db_session.rollback()


def test_triple_evidence_cannot_move_off_a_suppressed_episode(
    db_session: Session,
) -> None:
    """Evidence kept by a takedown cannot be re-attributed to another episode."""
    graph = seed_catalog_graph(db_session)
    open_episode = _open_episode(db_session, graph.podcast_id)
    upsert_graph_triple(
        db=db_session,
        payload=GraphTripleInputData(
            subject_media_id=graph.media_id,
            predicate="discussed",
            object_value="kept fact",
            source="rebuild",
            provenance_episode_id=graph.episode_id,
            evidence_text="suppressed evidence",
        ),
    )
    db_session.commit()
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    triple = db_session.query(GraphTriple).one()

    triple.provenance_episode_id = open_episode
    with pytest.raises(ContentSuppressedError, match="evidence moved off"):
        db_session.commit()
    db_session.rollback()


def test_bulk_suppression_insert_resets_remembered_checks(db_session: Session) -> None:
    """A suppression written by a bulk statement applies to later writes."""
    graph = seed_catalog_graph(db_session)
    open_episode = _open_episode(db_session, graph.podcast_id)
    request_case = _unrelated_case(db_session, graph.podcast_id)
    db_session.add(Mention(episode_id=open_episode, media_id=graph.media_id))
    db_session.flush()
    db_session.execute(
        insert(ContentSuppression).values(
            suppression_key=f"episode:{open_episode}:mentions",
            takedown_request_id=request_case,
            subject_type="episode",
            scope="mentions",
            episode_id=open_episode,
            created_at=_AT,
        ),
    )
    db_session.add(Mention(episode_id=open_episode, media_id=graph.media_id))

    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()


def _unrelated_case(db: Session, podcast_id: int) -> int:
    """Approve an unrelated takedown to own a bulk-written suppression row.

    Returns:
        The case id.
    """
    other = _open_episode(db, podcast_id, title="Unrelated")
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=other,
        actions=["suppress_raw_transcript"],
    )
    case_id: int = (
        db.query(TakedownRequest).order_by(TakedownRequest.id.desc()).one().id
    )
    return case_id


def test_savepoint_end_forgets_remembered_checks(db_session: Session) -> None:
    """A check made inside a savepoint is made again after the savepoint ends."""
    graph = seed_catalog_graph(db_session)
    engine = db_session.get_bind()
    checks: list[str] = []

    def record(*args: object) -> None:
        if "content_suppressions" in str(args[2]):
            checks.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        savepoint = db_session.begin_nested()
        db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
        db_session.flush()
        savepoint.rollback()
        checks.clear()
        db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
        db_session.flush()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    db_session.rollback()

    assert_that(checks).is_not_empty()


def _summaries(db: Session, *media_ids: int) -> None:
    for media_id in media_ids:
        db.add(
            MediaSummary(
                media_id=media_id,
                summary_key=f"media:{media_id}",
                summary_kind="overview",
                pipeline_version="v1",
                prompt_version="p1",
                source_text_hash="f" * 64,
                source_model="model",
                summary_text=f"summary {media_id}",
                generated_at=_AT,
            ),
        )
    db.commit()


def test_mention_takedown_with_derivatives_drops_every_episode_media_summary(
    db_session: Session,
) -> None:
    """A derivatives scope on a mention covers its episode: all its media."""
    graph = seed_catalog_graph(db_session)
    other = Media(type=MediaType.MOVIE, title="Arrival")
    unrelated = Media(type=MediaType.MOVIE, title="Unrelated")
    db_session.add_all([other, unrelated])
    db_session.flush()
    db_session.add(Mention(episode_id=graph.episode_id, media_id=other.id))
    db_session.commit()
    _summaries(db_session, graph.media_id, other.id, unrelated.id)

    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=graph.mention_id,
        actions=["unpublish_mentions", "suppress_derivatives"],
    )

    remaining = [row.media_id for row in db_session.query(MediaSummary)]
    assert_that(remaining).is_equal_to([unrelated.id])


def test_podcast_takedown_drops_summaries_of_media_in_every_episode(
    db_session: Session,
) -> None:
    """A podcast takedown covers the media mentioned in each of its episodes."""
    graph = seed_catalog_graph(db_session)
    second = _open_episode(db_session, graph.podcast_id, title="Second")
    other = Media(type=MediaType.MOVIE, title="Arrival")
    elsewhere = Podcast(name="Elsewhere", slug="elsewhere")
    db_session.add_all([other, elsewhere])
    db_session.flush()
    db_session.add(Mention(episode_id=second, media_id=other.id))
    db_session.commit()
    _summaries(db_session, graph.media_id, other.id)

    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=["suppress_derivatives"],
    )

    assert_that(db_session.query(MediaSummary).count()).is_zero()
