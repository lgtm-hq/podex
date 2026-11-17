"""PostgreSQL: a suppression survives its episode's move and its subject's delete.

An episode move locks the episode row ``FOR NO KEY UPDATE`` and reads the
podcast the database says it is in, then locks that podcast ``FOR SHARE``
before reading what to carry (shared order: media rows, episode rows, podcast
rows). Two moves of one episode therefore run one after the other, and a
takedown of the podcast it leaves, which locks it ``FOR UPDATE``, runs either
wholly before the move (and the move carries its suppressions) or wholly after
it (and no longer covers the episode). The ``RESTRICT`` foreign keys refuse
deletes that skip the flush guard, and revision ``0026`` round-trips with rows
present.
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
    Mention,
    Podcast,
    TakedownSubjectType,
    suppression_guard,
)
from podex.services import content_suppression
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
_BOTH = ["unpublish_mentions", "suppress_derivatives"]
_DELETE_RULES = text(
    "SELECT conname, confdeltype FROM pg_constraint"
    " WHERE conrelid = 'content_suppressions'::regclass AND contype = 'f'"
    " AND conname IN ('content_suppressions_podcast_id_fkey',"
    " 'content_suppressions_episode_id_fkey') ORDER BY conname",
)


@dataclass(frozen=True, slots=True)
class _Shows:
    """A podcast with an episode and its mention, and a second podcast."""

    podcast: int
    other: int
    episode: int
    media: int


def _seed(postgres: PostgresDatabase) -> _Shows:
    with postgres.sessions() as db:
        graph = seed_catalog_graph(db)
        other = Podcast(name="Other Show", slug="other-show")
        db.add(other)
        db.commit()
        return _Shows(
            podcast=graph.podcast_id,
            other=other.id,
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
        The number of suppressions the episode has after the flush.
    """
    episode = db.get_one(Episode, episode_id)
    episode.podcast_id = podcast_id
    db.flush()
    carried: int = (
        db.query(ContentSuppression)
        .filter(ContentSuppression.episode_id == episode_id)
        .count()
    )
    return carried


def _takedown_first(postgres: PostgresDatabase) -> _Shows:
    """Hold a podcast takedown open; the move waits, then carries it."""
    shows = _seed(postgres)
    unpublished, carried = run_overlapping(
        postgres,
        first=partial(_take_down_podcast, podcast_id=shows.podcast),
        second=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
        expected=_MOVE_WAITS,
    )
    assert_that(unpublished).is_equal_to(1)
    assert_that(carried).is_equal_to(2)
    return shows


def test_move_waiting_on_a_takedown_carries_its_suppressions(
    postgres: PostgresDatabase,
) -> None:
    """The moved episode stays suppressed for both scopes in its new podcast."""
    shows = _takedown_first(postgres)

    with postgres.sessions() as db:
        assert_that(db.get_one(Episode, shows.episode).podcast_id).is_equal_to(
            shows.other,
        )
        for scope in ContentSuppressionScope:
            assert_that(
                content_suppression.is_episode_suppressed(
                    db=db,
                    episode_id=shows.episode,
                    scope=scope,
                ),
            ).described_as(scope.value).is_true()
        db.add(Mention(episode_id=shows.episode, media_id=shows.media))
        with pytest.raises(ContentSuppressedError):
            db.commit()


def test_takedown_waiting_on_a_move_no_longer_covers_the_episode(
    postgres: PostgresDatabase,
) -> None:
    """A move that locked first commits; the takedown then covers what is left."""
    shows = _seed(postgres)

    carried, unpublished = run_overlapping(
        postgres,
        first=partial(_move, episode_id=shows.episode, podcast_id=shows.other),
        second=partial(_take_down_podcast, podcast_id=shows.podcast),
        expected=_TAKEDOWN_WAITS,
    )

    assert_that(carried).is_zero()
    assert_that(unpublished).is_zero()
    with postgres.sessions() as db:
        assert_that(
            content_suppression.is_episode_suppressed(
                db=db,
                episode_id=shows.episode,
                scope=ContentSuppressionScope.MENTIONS,
            ),
        ).is_false()
        assert_that(db.query(Mention).count()).is_equal_to(1)


def _skip_lock(**_kwargs: object) -> None:
    """Stand in for the move's podcast lock and take none."""


def test_move_without_its_podcast_lock_fails(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the lock the move does not wait and would leave the takedown behind."""
    monkeypatch.setattr(suppression_guard, "lock_podcasts_of_episodes", _skip_lock)

    with pytest.raises(AssertionError, match="never waited on the expected lock"):
        _takedown_first(postgres)


_EPISODE_LOCK_WAITS = ExpectedWait(
    statement_patterns=(r"\bFROM episodes\b", r"\bFOR NO KEY UPDATE\b"),
    row_lock_table="episodes",
)


def _seed_suppressed_podcast(postgres: PostgresDatabase) -> tuple[_Shows, int]:
    """Seed the shows and a third podcast taken down for both scopes.

    Returns:
        The shows and the suppressed podcast's id.
    """
    shows = _seed(postgres)
    with postgres.sessions() as db:
        suppressed = Podcast(name="Suppressed", slug="suppressed")
        db.add(suppressed)
        db.commit()
        approve_takedown(
            db,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=suppressed.id,
            actions=_BOTH,
        )
        suppressed_id: int = suppressed.id
    return shows, suppressed_id


def _stale_move(db: Session, *, episode_id: int, podcast_id: int) -> int:
    """Load the episode, assign it another podcast and flush.

    The copy is loaded while another transaction's move of it is still
    uncommitted, so it names the podcast the episode is leaving there.

    Returns:
        The number of suppressions the episode has after the flush.
    """
    return _move(db, episode_id=episode_id, podcast_id=podcast_id)


def _stale_copy_race(postgres: PostgresDatabase) -> tuple[_Shows, int]:
    """Move an episode into a suppressed podcast; a stale move waits behind it."""
    shows, suppressed = _seed_suppressed_podcast(postgres)
    _, carried = run_overlapping(
        postgres,
        first=partial(_move, episode_id=shows.episode, podcast_id=suppressed),
        second=partial(_stale_move, episode_id=shows.episode, podcast_id=shows.other),
        expected=_EPISODE_LOCK_WAITS,
    )
    assert_that(carried).is_equal_to(2)
    return shows, suppressed


def test_concurrent_moves_serialise_and_carry_from_the_persisted_podcast(
    postgres: PostgresDatabase,
) -> None:
    """The second move waits on the episode row, then carries what it really left."""
    shows, _ = _stale_copy_race(postgres)

    with postgres.sessions() as db:
        assert_that(db.get_one(Episode, shows.episode).podcast_id).is_equal_to(
            shows.other,
        )
        for scope in ContentSuppressionScope:
            assert_that(
                content_suppression.is_episode_suppressed(
                    db=db,
                    episode_id=shows.episode,
                    scope=scope,
                ),
            ).described_as(scope.value).is_true()
        db.add(Mention(episode_id=shows.episode, media_id=shows.media))
        with pytest.raises(ContentSuppressedError):
            db.commit()


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
    monkeypatch.setattr(suppression_guard, "lock_moved_episodes", _read_without_lock)

    with pytest.raises(AssertionError, match="never waited on the expected lock"):
        _stale_copy_race(postgres)


def _hold_podcast_for_edit(db: Session, *, podcast_id: int) -> int:
    """Lock a podcast row in the mode an operator's update takes, and hold it.

    ``FOR NO KEY UPDATE`` is what an ``UPDATE`` of the row takes; locking
    without updating lets the waiter show the tuple lock the race helper
    observes.

    Returns:
        The podcast id.
    """
    db.execute(
        select(Podcast.id)
        .where(Podcast.id == podcast_id)
        .with_for_update(key_share=True),
    ).one()
    return podcast_id


def _move_with_mention(db: Session, *, shows: _Shows) -> int:
    """Move the episode and write a mention of it in the same flush.

    Returns:
        The mention id.
    """
    db.get_one(Episode, shows.episode).podcast_id = shows.other
    mention = Mention(episode_id=shows.episode, media_id=shows.media)
    db.add(mention)
    db.flush()
    mention_id: int = mention.id
    return mention_id


def test_moved_episode_locks_the_podcast_it_joins_after_the_write(
    postgres: PostgresDatabase,
) -> None:
    """The check after the write locks the new podcast, not the cached old one.

    An operator edit holds the destination podcast; the moving flush's own
    foreign-key check does not conflict with it, so only the guard's
    ``FOR SHARE`` lock of the podcast the episode joined makes it wait.
    """
    shows = _seed(postgres)

    _, mention_id = run_overlapping(
        postgres,
        first=partial(_hold_podcast_for_edit, podcast_id=shows.other),
        second=partial(_move_with_mention, shows=shows),
        expected=_MOVE_WAITS,
    )

    assert_that(mention_id).is_positive()


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
