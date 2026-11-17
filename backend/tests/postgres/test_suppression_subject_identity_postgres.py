"""PostgreSQL: moves near a suppression and deletes under one are refused.

A move locks the podcasts it leaves (as the session remembers it) and joins
``FOR SHARE``, then the episode row ``FOR NO KEY UPDATE``, reading where the
episode really is. A takedown of either podcast takes it ``FOR UPDATE``, so
the two run one after the other: a move that waits is then refused, and a
takedown that waits then covers what the podcast holds. Discovery updates a
podcast row and then its episode rows; the move takes the same order, so
neither race deadlocks. The ``RESTRICT`` foreign keys refuse deletes that skip
the flush guard, and revision ``0026`` round-trips with rows present.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial

import pytest
from alembic.command import downgrade, upgrade
from assertpy import assert_that
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    Episode,
    Podcast,
    TakedownSubjectType,
    suppression_identity,
)
from podex.services import content_suppression
from podex.services.content_suppression import RetryableLockError
from tests.conftest import seed_catalog_graph
from tests.postgres.concurrency import ExpectedWait, row_lock_wait, run_overlapping
from tests.postgres.conftest import PostgresDatabase, alembic_config
from tests.takedown_support import approve_takedown, execute_takedown

pytestmark = pytest.mark.postgres

_MOVE_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM podcasts\b", r"\bFOR SHARE\b"),
    row_lock_table="podcasts",
)
_TAKEDOWN_WAITS = row_lock_wait("podcasts")
# A row another transaction updated (not only locked) is waited for on that
# transaction, without a tuple lock; the statement and blocker identify it.
_MOVE_WAITS_ON_UPDATED_PODCAST = ExpectedWait(
    statement_patterns=(r"\bFROM podcasts\b", r"\bFOR SHARE\b"),
    row_lock_table=None,
)
_UPDATE_WAITS_ON_MOVE = ExpectedWait(
    statement_patterns=(r"\bUPDATE podcasts\b",),
    row_lock_table=None,
)
_EPISODE_LOCK_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM episodes\b", r"\bFOR NO KEY UPDATE\b"),
    row_lock_table=None,
)
_BOTH = ["unpublish_mentions", "suppress_derivatives"]
_DELETE_RULES = text(
    "SELECT conname, confdeltype FROM pg_constraint"
    " WHERE conrelid = 'content_suppressions'::regclass AND contype = 'f'"
    " AND conname IN ('content_suppressions_podcast_id_fkey',"
    " 'content_suppressions_episode_id_fkey') ORDER BY conname",
)


@dataclass(frozen=True, slots=True)
class _Shows:
    """A podcast with an episode and its mention, and two more podcasts."""

    podcast: int
    other: int
    third: int
    episode: int
    media: int


def _seed(postgres: PostgresDatabase) -> _Shows:
    with postgres.sessions() as db:
        graph = seed_catalog_graph(db)
        other = Podcast(name="Other Show", slug="other-show")
        third = Podcast(name="Third Show", slug="third-show")
        db.add_all([other, third])
        db.commit()
        return _Shows(
            podcast=graph.podcast_id,
            other=other.id,
            third=third.id,
            episode=graph.episode_id,
            media=graph.media_id,
        )


def _take_down_podcast(db: Session, *, podcast_id: int) -> int:
    """Approve and execute a podcast takedown without committing.

    Returns:
        The number of mentions it unpublished.
    """
    result = execute_takedown(
        db,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=podcast_id,
        actions=_BOTH,
    )
    unpublished: int = result.mentions_unpublished
    return unpublished


def _move(db: Session, *, episode_id: int, podcast_id: int) -> int:
    """Move an episode to another podcast and flush without committing.

    Returns:
        The podcast the episode was moved to.
    """
    db.get_one(Episode, episode_id).podcast_id = podcast_id
    db.flush()
    return podcast_id


def _suppressed(postgres: PostgresDatabase, episode_id: int) -> bool:
    with postgres.sessions() as db:
        suppressed: bool = content_suppression.is_episode_suppressed(
            db=db,
            episode_id=episode_id,
            scope=ContentSuppressionScope.MENTIONS,
        )
        return suppressed


def _podcast_of(postgres: PostgresDatabase, episode_id: int) -> int:
    with postgres.sessions() as db:
        podcast_id: int = db.get_one(Episode, episode_id).podcast_id
        return podcast_id


@pytest.mark.parametrize("side", ["origin", "destination"])
def test_move_waiting_on_a_takedown_is_refused(
    postgres: PostgresDatabase,
    side: str,
) -> None:
    """A takedown of either podcast holds it; the move waits, then is refused."""
    shows = _seed(postgres)
    taken_down = shows.podcast if side == "origin" else shows.other

    with pytest.raises(ContentSuppressedError, match=f"Episode {shows.episode} "):
        run_overlapping(
            postgres,
            first=partial(_take_down_podcast, podcast_id=taken_down),
            second=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
            expected=_MOVE_WAITS,
        )

    assert_that(_podcast_of(postgres, shows.episode)).is_equal_to(shows.podcast)
    assert_that(_suppressed(postgres, shows.episode)).is_equal_to(side == "origin")


def test_takedown_of_the_origin_waiting_on_a_move_no_longer_covers_it(
    postgres: PostgresDatabase,
) -> None:
    """A move that locked first commits; the takedown covers what is left."""
    shows = _seed(postgres)

    _, unpublished = run_overlapping(
        postgres,
        first=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
        second=partial(_take_down_podcast, podcast_id=shows.podcast),
        expected=_TAKEDOWN_WAITS,
    )

    assert_that(unpublished).is_zero()
    assert_that(_suppressed(postgres, shows.episode)).is_false()


def test_takedown_of_the_destination_waiting_on_a_move_covers_it(
    postgres: PostgresDatabase,
) -> None:
    """The takedown waits, sees the episode arrive, refuses, and covers it on retry.

    Its covered mentions changed while it waited, which it answers with its
    retryable error; run again, it takes the moved episode down.
    """
    shows = _seed(postgres)

    with pytest.raises(RetryableLockError):
        run_overlapping(
            postgres,
            first=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
            second=partial(_take_down_podcast, podcast_id=shows.other),
            expected=_TAKEDOWN_WAITS,
        )
    with postgres.sessions() as db:
        unpublished = _take_down_podcast(db, podcast_id=shows.other)
        db.commit()

    assert_that(unpublished).is_equal_to(1)
    assert_that(_suppressed(postgres, shows.episode)).is_true()


def _discovery_update(db: Session, *, shows: _Shows) -> int:
    """Update a podcast row and then one of its episodes in one flush.

    Returns:
        The episode id.
    """
    db.get_one(Podcast, shows.podcast).name = "Renamed by discovery"
    db.get_one(Episode, shows.episode).title = "Retitled by discovery"
    db.flush()
    return shows.episode


def test_move_waiting_on_discovery_does_not_deadlock(
    postgres: PostgresDatabase,
) -> None:
    """Discovery holds the podcast and the episode; the move waits, then runs."""
    shows = _seed(postgres)

    _, destination = run_overlapping(
        postgres,
        first=partial(_discovery_update, shows=shows),
        second=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
        expected=_MOVE_WAITS_ON_UPDATED_PODCAST,
    )

    assert_that(_podcast_of(postgres, shows.episode)).is_equal_to(destination)


def test_discovery_waiting_on_a_move_does_not_deadlock(
    postgres: PostgresDatabase,
) -> None:
    """The move holds both podcasts and the episode; discovery waits, then runs."""
    shows = _seed(postgres)

    _, episode_id = run_overlapping(
        postgres,
        first=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
        second=partial(_discovery_update, shows=shows),
        expected=_UPDATE_WAITS_ON_MOVE,
    )

    with postgres.sessions() as db:
        episode = db.get_one(Episode, episode_id)
        assert_that(episode.podcast_id).is_equal_to(shows.other)
        assert_that(episode.title).is_equal_to("Retitled by discovery")


def _stale_copy_race(postgres: PostgresDatabase) -> _Shows:
    """Move an episode; a copy loaded before that move commits waits behind it.

    The second session loads the episode while the first move is uncommitted,
    so its copy names the podcast the episode is leaving there. It locks the
    podcasts it names, then waits on the episode row.
    """
    shows = _seed(postgres)
    with pytest.raises(RetryableLockError, match=f"Episode {shows.episode} "):
        run_overlapping(
            postgres,
            first=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
            second=partial(_move, episode_id=shows.episode, podcast_id=shows.third),
            expected=_EPISODE_LOCK_WAITS,
        )
    return shows


def test_concurrent_moves_serialise_and_the_stale_one_is_refused(
    postgres: PostgresDatabase,
) -> None:
    """Scenario A: the second move waits on the episode row, then is refused."""
    shows = _stale_copy_race(postgres)

    assert_that(_podcast_of(postgres, shows.episode)).is_equal_to(shows.other)


def _read_without_lock(
    *,
    session: Session,
    episode_ids: Iterable[int],
) -> dict[int, int]:
    """Stand in for the episode lock: read the podcasts without locking."""
    rows = session.execute(
        select(Episode.id, Episode.podcast_id).where(Episode.id.in_(list(episode_ids))),
    )
    return {int(row.id): int(row.podcast_id) for row in rows}


def test_concurrent_move_without_the_episode_lock_fails(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the episode lock the stale move reads the old podcast and races."""
    monkeypatch.setattr(suppression_identity, "lock_moved_episodes", _read_without_lock)

    with pytest.raises(AssertionError, match="never waited on the expected lock"):
        _stale_copy_race(postgres)


def test_stale_copy_after_a_takedown_of_the_new_podcast_is_refused(
    postgres: PostgresDatabase,
) -> None:
    """Scenario B: moved and taken down elsewhere since it was loaded; refused."""
    shows = _seed(postgres)
    stale = postgres.sessions()
    episode = stale.get_one(Episode, shows.episode)
    with postgres.sessions() as db:
        _move(db, episode_id=shows.episode, podcast_id=shows.other)
        db.commit()
    with postgres.sessions() as db:
        _take_down_podcast(db, podcast_id=shows.other)
        db.commit()

    episode.podcast_id = shows.third
    with pytest.raises(RetryableLockError, match=f"Episode {shows.episode} "):
        stale.flush()
    stale.close()

    assert_that(_podcast_of(postgres, shows.episode)).is_equal_to(shows.other)
    assert_that(_suppressed(postgres, shows.episode)).is_true()


def _delete_podcast(db: Session, *, podcast_id: int) -> int:
    """Delete a podcast through the ORM and flush without committing.

    Returns:
        The deleted podcast id.
    """
    db.delete(db.get_one(Podcast, podcast_id))
    db.flush()
    return podcast_id


def test_podcast_delete_waiting_on_its_takedown_is_refused(
    postgres: PostgresDatabase,
) -> None:
    """The delete locks the podcast before reading its coverage, then refuses.

    Without that lock it would read no suppression, wait at its ``DELETE``,
    and fail on the foreign key instead.
    """
    shows = _seed(postgres)

    with pytest.raises(ContentSuppressedError, match=f"Podcast {shows.third} "):
        run_overlapping(
            postgres,
            first=partial(_take_down_podcast, podcast_id=shows.third),
            second=partial(_delete_podcast, podcast_id=shows.third),
            expected=_MOVE_WAITS,
        )

    with postgres.sessions() as db:
        assert_that(db.get(Podcast, shows.third)).is_not_none()


def _take_down_episode(postgres: PostgresDatabase, shows: _Shows) -> None:
    with postgres.sessions() as db:
        approve_takedown(
            db,
            subject_type=TakedownSubjectType.EPISODE,
            subject_id=shows.episode,
            actions=["suppress_derivatives"],
        )


@pytest.mark.parametrize("subject", ["podcast", "episode"])
def test_deletes_of_a_suppressed_subject_are_refused(
    postgres: PostgresDatabase,
    subject: str,
) -> None:
    """The guard refuses the ORM delete and the foreign key refuses a Core one."""
    shows = _seed(postgres)
    _take_down_episode(postgres, shows)
    model = Podcast if subject == "podcast" else Episode
    row_id = shows.podcast if subject == "podcast" else shows.episode

    with postgres.sessions() as db:
        db.delete(db.get_one(model, row_id))
        with pytest.raises(ContentSuppressedError, match=f"{model.__name__} {row_id}"):
            db.flush()
    with postgres.sessions() as db:
        with pytest.raises(IntegrityError):
            db.execute(delete(model).where(model.id == row_id))
    with postgres.sessions() as db:
        assert_that(db.query(ContentSuppression).count()).is_equal_to(1)


def _delete_rules(postgres: PostgresDatabase) -> list[tuple[object, ...]]:
    with postgres.engine.connect() as connection:
        return [tuple(row) for row in connection.execute(_DELETE_RULES)]


def test_revision_0026_round_trips_with_suppressions_on_postgres(
    postgres: PostgresDatabase,
) -> None:
    """Downgrade restores cascade, upgrade restricts again, rows are kept."""
    shows = _seed(postgres)
    _take_down_episode(postgres, shows)
    assert_that(_delete_rules(postgres)).is_equal_to(
        [
            ("content_suppressions_episode_id_fkey", "r"),
            ("content_suppressions_podcast_id_fkey", "r"),
        ],
    )
    config = alembic_config(postgres.url)

    downgrade(config, "0025")

    assert_that(_delete_rules(postgres)).is_equal_to(
        [
            ("content_suppressions_episode_id_fkey", "c"),
            ("content_suppressions_podcast_id_fkey", "c"),
        ],
    )
    upgrade(config, "head")
    assert_that(_delete_rules(postgres)).is_equal_to(
        [
            ("content_suppressions_episode_id_fkey", "r"),
            ("content_suppressions_podcast_id_fkey", "r"),
        ],
    )
    with postgres.sessions() as db:
        assert_that(db.query(ContentSuppression).count()).is_equal_to(1)
