"""PostgreSQL: a suppression survives its episode's move and its subject's delete.

An episode move locks the podcast it leaves ``FOR SHARE`` before reading what
to carry, in the shared order (media rows, then podcast rows), so a takedown
of that podcast, which locks it ``FOR UPDATE``, runs either wholly before the
move (and the move carries its suppressions) or wholly after it (and no
longer covers the episode). The ``RESTRICT`` foreign keys refuse deletes that
skip the flush guard, and revision ``0025`` round-trips with rows present.
"""

from dataclasses import dataclass
from functools import partial

import pytest
from alembic.command import downgrade, upgrade
from assertpy import assert_that
from sqlalchemy import delete, text
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


def test_revision_0025_round_trips_with_suppressions_on_postgres(
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

    downgrade(config, "0024")

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
