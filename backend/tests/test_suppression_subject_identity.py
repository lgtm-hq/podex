"""A suppression stays in force: moves near it and deletes under it are refused.

Suppressions are keyed by row ids. A change of an episode's podcast is
refused when the podcast it leaves or the one it joins has a suppression,
stored or pending in the session; an episode's own suppressions follow its
id. Deleting a row a suppression covers is refused by the flush guard and,
for writes around it, where a suppression names the row, by the
``RESTRICT`` foreign keys.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    ContentSuppressionSubjectType,
    Episode,
    Mention,
    Podcast,
    TakedownRequest,
    TakedownSubjectType,
)
from podex.services.content_suppression import is_episode_suppressed
from podex.services.takedown_requests import execute_approved_takedown_request
from tests.conftest import SeededGraph, seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    recorded_statements,
    submit_takedown,
    take_down_episode,
    take_down_mention,
)

_BOTH = ["unpublish_mentions", "suppress_derivatives"]


@dataclass(frozen=True, slots=True)
class _Shows:
    """A suppressed podcast with one episode, two open podcasts and a media record."""

    db: Session
    suppressed: int
    open: int
    spare: int
    episode: int
    media: int


def _shows(db: Session) -> _Shows:
    """Seed three podcasts and take the first one down."""
    graph = seed_catalog_graph(db)
    other = Podcast(name="Other Show", slug="other-show")
    spare = Podcast(name="Spare Show", slug="spare-show")
    db.add_all([other, spare])
    db.commit()
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=_BOTH,
    )
    return _Shows(
        db=db,
        suppressed=graph.podcast_id,
        open=other.id,
        spare=spare.id,
        episode=graph.episode_id,
        media=graph.media_id,
    )


def _episode(shows: _Shows) -> Episode:
    episode: Episode = shows.db.get_one(Episode, shows.episode)
    return episode


def _move_by_column(shows: _Shows) -> Episode:
    episode = _episode(shows)
    episode.podcast_id = shows.open
    return episode


def _move_by_relationship(shows: _Shows) -> Episode:
    episode = _episode(shows)
    episode.podcast = shows.db.get_one(Podcast, shows.open)
    return episode


def _move_by_collection(shows: _Shows) -> Episode:
    episode = _episode(shows)
    podcast = shows.db.get_one(Podcast, shows.open)
    podcast.episodes.append(episode)
    return episode


def _move_to_new_podcast(shows: _Shows) -> Episode:
    episode = _episode(shows)
    episode.podcast = Podcast(name="New Show", slug="new-show")
    return episode


_MOVES: dict[str, Callable[[_Shows], Episode]] = {
    "column": _move_by_column,
    "relationship": _move_by_relationship,
    "collection": _move_by_collection,
    "new-podcast": _move_to_new_podcast,
}


@pytest.mark.parametrize("move", _MOVES.values(), ids=_MOVES.keys())
def test_moving_out_of_a_suppressed_podcast_is_refused(
    db_session: Session,
    move: Callable[[_Shows], Episode],
) -> None:
    """However the podcast is assigned, leaving a suppressed one is refused."""
    shows = _shows(db_session)
    kept = move(shows)

    with pytest.raises(
        ContentSuppressedError,
        match=rf"refused write: Episode {shows.episode} \(moved",
    ):
        db_session.commit()
    db_session.rollback()

    assert_that(kept.podcast_id).is_equal_to(shows.suppressed)


def test_moving_into_a_suppressed_podcast_is_refused(db_session: Session) -> None:
    """Joining a suppressed podcast is refused too; nothing in the product does it."""
    shows = _shows(db_session)
    newcomer = Episode(podcast_id=shows.open, title="Newcomer")
    db_session.add(newcomer)
    db_session.commit()

    newcomer.podcast_id = shows.suppressed
    with pytest.raises(ContentSuppressedError, match=f"Episode {newcomer.id} "):
        db_session.commit()


def _approve_without_executing(
    db: Session,
    *,
    subject_type: TakedownSubjectType,
    subject_id: int,
) -> TakedownRequest:
    """Approve a takedown and commit, without executing it."""
    request = submit_takedown(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        actions=["unpublish_mentions"],
    )
    db.commit()
    return request


def _execute(db: Session, request: TakedownRequest) -> None:
    execute_approved_takedown_request(db=db, request=request, artifact_store=None)


def _seed_with_other_podcast(db: Session) -> tuple[SeededGraph, int]:
    """Seed the catalog graph and a second, open podcast."""
    graph = seed_catalog_graph(db)
    other = Podcast(name="Other Show", slug="other-show")
    db.add(other)
    db.commit()
    other_id: int = other.id
    return graph, other_id


@pytest.mark.parametrize("side", ["origin", "destination"])
def test_a_suppression_pending_in_the_session_refuses_the_move(
    db_session: Session,
    side: str,
) -> None:
    """A podcast suppression added but not yet written counts as a stored one."""
    graph, other_id = _seed_with_other_podcast(db_session)
    request = _approve_without_executing(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
    )
    suppressed = graph.podcast_id if side == "origin" else other_id
    db_session.add(
        ContentSuppression(
            suppression_key=f"podcast:{suppressed}:mentions",
            takedown_request_id=request.id,
            subject_type=ContentSuppressionSubjectType.PODCAST.value,
            scope=ContentSuppressionScope.MENTIONS.value,
            podcast_id=suppressed,
        ),
    )
    db_session.get_one(Episode, graph.episode_id).podcast_id = other_id

    with pytest.raises(ContentSuppressedError, match=f"Episode {graph.episode_id} "):
        db_session.flush()


def _mentions_suppressed(db: Session, episode_id: int) -> bool:
    suppressed: bool = is_episode_suppressed(
        db=db,
        episode_id=episode_id,
        scope=ContentSuppressionScope.MENTIONS,
    )
    return suppressed


def test_takedown_executed_with_a_pending_move_refuses_it(
    db_session: Session,
) -> None:
    """Executing the takedown flushes the move; the flush refuses it.

    Nothing is committed, and once the move is undone the same takedown runs.
    """
    graph, other_id = _seed_with_other_podcast(db_session)
    request = _approve_without_executing(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
    )
    db_session.get_one(Episode, graph.episode_id).podcast_id = other_id

    with pytest.raises(ContentSuppressedError, match=f"Episode {graph.episode_id} "):
        _execute(db_session, request)
    db_session.rollback()

    assert_that(_mentions_suppressed(db_session, graph.episode_id)).is_false()
    _execute(db_session, request)
    db_session.commit()
    episode = db_session.get_one(Episode, graph.episode_id)
    assert_that(episode.podcast_id).is_equal_to(graph.podcast_id)
    assert_that(_mentions_suppressed(db_session, graph.episode_id)).is_true()


def test_moving_between_open_podcasts_is_allowed(db_session: Session) -> None:
    """Moves no podcast suppression is involved in write nothing else."""
    graph = seed_catalog_graph(db_session)
    other = Podcast(name="Other Show", slug="other-show")
    db_session.add(other)
    db_session.commit()

    db_session.get_one(Episode, graph.episode_id).podcast_id = other.id
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db_session.commit()

    assert_that(db_session.query(ContentSuppression).count()).is_zero()


def test_an_episodes_own_suppression_follows_it_between_open_podcasts(
    db_session: Session,
) -> None:
    """Episode and pair suppressions are keyed by the episode; they do not block."""
    graph = seed_catalog_graph(db_session)
    other = Podcast(name="Other Show", slug="other-show")
    db_session.add(other)
    db_session.commit()
    take_down_episode(db_session, graph.episode_id, *_BOTH)

    db_session.get_one(Episode, graph.episode_id).podcast_id = other.id
    db_session.commit()

    for scope in ContentSuppressionScope:
        assert_that(
            is_episode_suppressed(
                db=db_session,
                episode_id=graph.episode_id,
                scope=scope,
            ),
        ).described_as(scope.value).is_true()


def test_partial_flush_of_a_move_is_refused(db_session: Session) -> None:
    """Flushing only the episode does not skip the rule."""
    shows = _shows(db_session)
    episode = _move_by_column(shows)

    with pytest.raises(ContentSuppressedError, match=f"Episode {shows.episode} "):
        db_session.flush([episode])


def _delete_episode(db: Session, episode_id: int) -> None:
    db.delete(db.get_one(Episode, episode_id))


def _delete_podcast(db: Session, podcast_id: int) -> None:
    db.delete(db.get_one(Podcast, podcast_id))


def test_deleting_a_taken_down_episode_is_refused(db_session: Session) -> None:
    """The delete names the episode and the suppression stays."""
    graph = seed_catalog_graph(db_session)
    take_down_episode(db_session, graph.episode_id, *_BOTH)

    _delete_episode(db_session, graph.episode_id)
    with pytest.raises(
        ContentSuppressedError,
        match=rf"refused write: Episode {graph.episode_id} \(deleted",
    ):
        db_session.flush()
    db_session.rollback()

    assert_that(db_session.query(ContentSuppression).count()).is_equal_to(2)


def test_deleting_an_episode_of_a_suppressed_podcast_is_refused(
    db_session: Session,
) -> None:
    """A podcast suppression covers its episodes, so they cannot be deleted."""
    shows = _shows(db_session)

    _delete_episode(db_session, shows.episode)

    with pytest.raises(ContentSuppressedError, match=f"Episode {shows.episode}"):
        db_session.flush()


def test_deleting_an_episode_with_a_pair_suppression_is_refused(
    db_session: Session,
) -> None:
    """A mention-pair suppression also keeps its episode from being deleted."""
    graph = seed_catalog_graph(db_session)
    take_down_mention(db_session, graph.mention_id)

    _delete_episode(db_session, graph.episode_id)

    with pytest.raises(ContentSuppressedError, match=f"Episode {graph.episode_id}"):
        db_session.flush()


@pytest.mark.parametrize("subject", ["podcast", "episode"])
def test_deleting_a_podcast_a_suppression_covers_is_refused(
    db_session: Session,
    subject: str,
) -> None:
    """A podcast with its own or an episode's suppression cannot be deleted."""
    graph = seed_catalog_graph(db_session)
    subject_ids = {"podcast": graph.podcast_id, "episode": graph.episode_id}
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType(subject),
        subject_id=subject_ids[subject],
        actions=["unpublish_mentions"],
    )

    _delete_podcast(db_session, graph.podcast_id)

    with pytest.raises(
        ContentSuppressedError,
        match=rf"refused write: Podcast {graph.podcast_id} \(deleted",
    ):
        db_session.flush()


def test_deleting_unsuppressed_rows_is_allowed(db_session: Session) -> None:
    """Rows no suppression covers delete as before."""
    graph = seed_catalog_graph(db_session)
    spare = Podcast(name="Spare", slug="spare")
    db_session.add(spare)
    db_session.commit()
    lone = Episode(podcast_id=spare.id, title="Lone")
    db_session.add(lone)
    db_session.commit()

    _delete_episode(db_session, lone.id)
    db_session.commit()
    _delete_podcast(db_session, spare.id)
    db_session.commit()

    assert_that(db_session.get(Podcast, spare.id)).is_none()
    assert_that(db_session.get(Podcast, graph.podcast_id)).is_not_none()


def test_a_deleted_episode_id_cannot_be_reused_around_the_suppression(
    db_session: Session,
) -> None:
    """SQLite reuses the highest id; the suppressed episode keeps it instead.

    Deleting it is refused through the ORM and by the foreign key around it, so
    an episode created afterwards gets a new id and the suppressed one stays
    covered.
    """
    graph = seed_catalog_graph(db_session)
    take_down_episode(db_session, graph.episode_id, *_BOTH)

    _delete_episode(db_session, graph.episode_id)
    with pytest.raises(ContentSuppressedError):
        db_session.flush()
    db_session.rollback()
    with pytest.raises(IntegrityError):
        db_session.execute(delete(Episode).where(Episode.id == graph.episode_id))
    db_session.rollback()

    again = Episode(podcast_id=graph.podcast_id, title="Pilot")
    db_session.add(again)
    db_session.commit()
    assert_that(again.id).is_not_equal_to(graph.episode_id)
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    with pytest.raises(ContentSuppressedError):
        db_session.commit()


@pytest.mark.parametrize("subject", ["podcast", "episode"])
def test_database_refuses_to_cascade_a_suppression_away(
    db_session: Session,
    subject: str,
) -> None:
    """A delete around the guard fails on the foreign key, not silently."""
    graph = seed_catalog_graph(db_session)
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    statement = (
        delete(Podcast).where(Podcast.id == graph.podcast_id)
        if subject == "podcast"
        else delete(Episode).where(Episode.id == graph.episode_id)
    )

    with pytest.raises(IntegrityError):
        db_session.execute(statement)
    db_session.rollback()

    assert_that(db_session.query(ContentSuppression).count()).is_equal_to(1)


def test_partial_flush_of_a_delete_is_refused(db_session: Session) -> None:
    """Flushing only the deleted episode does not skip the rule."""
    graph = seed_catalog_graph(db_session)
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    episode = db_session.get_one(Episode, graph.episode_id)
    db_session.delete(episode)

    with pytest.raises(ContentSuppressedError, match=f"Episode {graph.episode_id} "):
        db_session.flush([episode])


@pytest.mark.parametrize("count", [1, 50])
def test_moves_and_deletes_add_a_fixed_number_of_statements(
    db_session: Session,
    count: int,
) -> None:
    """Moving or deleting many episodes runs the same number of guard reads."""
    graph = seed_catalog_graph(db_session)
    other = Podcast(name="Other Show", slug="other-show")
    episodes = [
        Episode(podcast_id=graph.podcast_id, title=f"Episode {index}")
        for index in range(count)
    ]
    db_session.add(other)
    db_session.add_all(episodes)
    db_session.commit()
    engine = db_session.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))
    episodes = (
        db_session.query(Episode).filter(Episode.podcast_id == graph.podcast_id).all()
    )
    for episode in episodes:
        episode.podcast_id = other.id
    with recorded_statements(engine) as moving:
        db_session.flush()

    # Podcast locks, episode-row locks with their persisted podcasts, and the
    # podcasts' suppressions; on SQLite also the no-op write, not a SELECT.
    assert_that(_selects(moving)).is_length(3)
    db_session.commit()

    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    for episode in episodes:
        db_session.delete(episode)
    with recorded_statements(engine) as deleting:
        with pytest.raises(ContentSuppressedError):
            db_session.flush()

    # Podcast locks and one coverage check.
    assert_that(_selects(deleting)).is_length(2)


def _selects(statements: list[str]) -> list[str]:
    return [item for item in statements if item.lstrip().upper().startswith("SELECT")]


@pytest.mark.parametrize("subject", ["podcast", "episode", "mention"])
def test_delete_pending_while_its_takedown_runs_is_refused(
    db_session: Session,
    subject: str,
) -> None:
    """The flush that records the suppression also refuses the pending delete.

    With autoflush off, the delete and the new suppression row reach the same
    flush; the pending row counts as much as a stored one.
    """
    graph = seed_catalog_graph(db_session)
    subject_ids = {
        "podcast": graph.podcast_id,
        "episode": graph.episode_id,
        "mention": graph.mention_id,
    }
    request = _approve_without_executing(
        db_session,
        subject_type=TakedownSubjectType(subject),
        subject_id=subject_ids[subject],
    )
    db_session.delete(db_session.get_one(Episode, graph.episode_id))

    with pytest.raises(ContentSuppressedError, match=f"Episode {graph.episode_id} "):
        _execute(db_session, request)


@pytest.mark.parametrize("subject", ["podcast", "episode"])
def test_podcast_delete_pending_while_its_takedown_runs_is_refused(
    db_session: Session,
    subject: str,
) -> None:
    """A pending suppression of the podcast or one of its episodes refuses it."""
    graph = seed_catalog_graph(db_session)
    subject_ids = {"podcast": graph.podcast_id, "episode": graph.episode_id}
    request = _approve_without_executing(
        db_session,
        subject_type=TakedownSubjectType(subject),
        subject_id=subject_ids[subject],
    )
    db_session.delete(db_session.get_one(Podcast, graph.podcast_id))

    with pytest.raises(ContentSuppressedError, match=f"Podcast {graph.podcast_id} "):
        _execute(db_session, request)


def test_assigning_the_same_podcast_again_is_not_a_move(db_session: Session) -> None:
    """Re-assigning an episode to its own suppressed podcast changes nothing."""
    shows = _shows(db_session)
    episode = _episode(shows)

    episode.podcast = db_session.get_one(Podcast, shows.suppressed)
    db_session.commit()

    assert_that(episode.podcast_id).is_equal_to(shows.suppressed)
