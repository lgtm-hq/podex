"""A suppression stays in force when its episode moves or its subject is deleted.

Suppressions are keyed by row ids. Moving an episode out of a suppressed
podcast carries the podcast's suppressions onto the episode in the same
flush, and deleting a row a suppression covers is refused by the flush guard
and, for writes around it, by the ``RESTRICT`` foreign keys.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, delete, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    Episode,
    EpisodeSummary,
    Mention,
    Podcast,
    TakedownSubjectType,
)
from podex.services.content_suppression import (
    count_published_mentions,
    is_episode_suppressed,
)
from tests.conftest import seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    recorded_statements,
    summary_values,
    take_down_episode,
    take_down_mention,
)

_BOTH = ["unpublish_mentions", "suppress_derivatives"]


@dataclass(frozen=True, slots=True)
class _Shows:
    """A suppressed podcast with one episode, an open podcast and a media record."""

    db: Session
    suppressed: int
    open: int
    episode: int
    media: int


def _shows(db: Session, actions: list[str] | None = None) -> _Shows:
    """Seed two podcasts and take the first one down with ``actions``."""
    graph = seed_catalog_graph(db)
    other = Podcast(name="Other Show", slug="other-show")
    db.add(other)
    db.commit()
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=actions or _BOTH,
    )
    return _Shows(
        db=db,
        suppressed=graph.podcast_id,
        open=other.id,
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


def _write_hidden_mention(shows: _Shows) -> None:
    """Write a mention around the guard, as only a bypass could."""
    shows.db.execute(
        insert(Mention).values(episode_id=shows.episode, media_id=shows.media),
    )
    shows.db.commit()


@pytest.mark.parametrize("move", _MOVES.values(), ids=_MOVES.keys())
def test_moved_episode_stays_suppressed(
    db_session: Session,
    move: Callable[[_Shows], Episode],
) -> None:
    """Moving an episode out of a suppressed podcast keeps it suppressed."""
    shows = _shows(db_session)
    _write_hidden_mention(shows)

    moved = move(shows)
    db_session.commit()

    assert_that(moved.podcast_id).is_not_equal_to(shows.suppressed)

    for scope in ContentSuppressionScope:
        assert_that(
            is_episode_suppressed(db=db_session, episode_id=shows.episode, scope=scope),
        ).described_as(scope.value).is_true()
    assert_that(
        count_published_mentions(db_session, Mention.episode_id == shows.episode),
    ).is_zero()
    db_session.add(Mention(episode_id=shows.episode, media_id=shows.media))
    with pytest.raises(ContentSuppressedError, match=r"\(written into the pair\)"):
        db_session.commit()
    db_session.rollback()
    db_session.add(EpisodeSummary(**summary_values(shows.episode, "k", "text")))
    with pytest.raises(ContentSuppressedError, match=r"\(written for the episode\)"):
        db_session.commit()


def test_moved_episode_carries_each_scope_with_its_takedown(
    db_session: Session,
) -> None:
    """The carried rows are episode suppressions of the same scopes and cases."""
    shows = _shows(db_session, ["suppress_derivatives"])
    podcast_row = db_session.query(ContentSuppression).one()

    _move_by_column(shows)
    db_session.commit()

    carried = (
        db_session.query(ContentSuppression)
        .filter(ContentSuppression.episode_id == shows.episode)
        .one()
    )
    assert_that(carried.subject_type).is_equal_to("episode")
    assert_that(carried.scope).is_equal_to("derivatives")
    assert_that(carried.takedown_request_id).is_equal_to(
        podcast_row.takedown_request_id,
    )
    assert_that(carried.suppression_key).is_equal_to(
        f"episode:{shows.episode}:derivatives",
    )
    db_session.add(Mention(episode_id=shows.episode, media_id=shows.media))
    db_session.commit()


def test_mention_written_in_the_moving_flush_is_refused(db_session: Session) -> None:
    """A mention added in the same flush as the move sees the carried row."""
    shows = _shows(db_session)

    _move_by_column(shows)
    db_session.add(Mention(episode_id=shows.episode, media_id=shows.media))

    with pytest.raises(ContentSuppressedError):
        db_session.commit()


def test_move_keeps_an_existing_episode_suppression(db_session: Session) -> None:
    """An episode already suppressed for a scope gets no second row for it."""
    shows = _shows(db_session, ["unpublish_mentions"])
    take_down_episode(db_session, shows.episode, "unpublish_mentions")

    _move_by_column(shows)
    db_session.commit()

    assert_that(
        db_session.query(ContentSuppression)
        .filter(ContentSuppression.episode_id == shows.episode)
        .count(),
    ).is_equal_to(1)


def test_moving_back_and_forth_keeps_the_suppression(db_session: Session) -> None:
    """Moving out, back in and out again stays suppressed and adds nothing."""
    shows = _shows(db_session)
    for podcast_id in (shows.open, shows.suppressed, shows.open):
        _episode(shows).podcast_id = podcast_id
        db_session.commit()

    assert_that(
        is_episode_suppressed(
            db=db_session,
            episode_id=shows.episode,
            scope=ContentSuppressionScope.MENTIONS,
        ),
    ).is_true()
    assert_that(db_session.query(ContentSuppression).count()).is_equal_to(4)


def test_moving_into_a_suppressed_podcast_is_allowed_and_hidden(
    db_session: Session,
) -> None:
    """An episode moved in is hidden by the podcast suppression, nothing is copied."""
    shows = _shows(db_session)
    newcomer = Episode(podcast_id=shows.open, title="Newcomer")
    db_session.add(newcomer)
    db_session.commit()
    db_session.add(Mention(episode_id=newcomer.id, media_id=shows.media))
    db_session.commit()

    newcomer.podcast_id = shows.suppressed
    db_session.commit()

    assert_that(
        count_published_mentions(db_session, Mention.episode_id == newcomer.id),
    ).is_zero()
    assert_that(
        db_session.query(ContentSuppression)
        .filter(ContentSuppression.episode_id == newcomer.id)
        .count(),
    ).is_zero()
    db_session.add(Mention(episode_id=newcomer.id, media_id=shows.media))
    with pytest.raises(ContentSuppressedError):
        db_session.commit()


def test_a_checked_episode_moved_into_a_suppressed_podcast_is_checked_again(
    db_session: Session,
) -> None:
    """A check that passed before the move is not reused after it."""
    shows = _shows(db_session)
    newcomer = Episode(podcast_id=shows.open, title="Newcomer")
    db_session.add(newcomer)
    db_session.commit()
    db_session.add(Mention(episode_id=newcomer.id, media_id=shows.media))
    db_session.flush()

    newcomer.podcast_id = shows.suppressed
    db_session.flush()
    db_session.add(Mention(episode_id=newcomer.id, media_id=shows.media))

    with pytest.raises(ContentSuppressedError):
        db_session.flush()


def test_moving_an_unsuppressed_episode_records_nothing(db_session: Session) -> None:
    """Moves between podcasts without suppressions write no suppression."""
    graph = seed_catalog_graph(db_session)
    other = Podcast(name="Other Show", slug="other-show")
    db_session.add(other)
    db_session.commit()

    db_session.get_one(Episode, graph.episode_id).podcast_id = other.id
    db_session.commit()

    assert_that(db_session.query(ContentSuppression).count()).is_zero()
    db_session.add(Mention(episode_id=graph.episode_id, media_id=graph.media_id))
    db_session.commit()


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
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=_BOTH,
    )
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

    # Podcast locks, the origin podcasts' suppressions, existing episode keys.
    assert_that(_selects(moving)).is_length(3)
    db_session.commit()

    for episode in episodes:
        db_session.delete(episode)
    with recorded_statements(engine) as deleting:
        with pytest.raises(ContentSuppressedError):
            db_session.flush()

    # Podcast locks and one coverage check.
    assert_that(_selects(deleting)).is_length(2)


def _selects(statements: list[str]) -> list[str]:
    return [item for item in statements if item.lstrip().upper().startswith("SELECT")]
