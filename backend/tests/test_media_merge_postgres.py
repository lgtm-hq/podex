"""PostgreSQL concurrency checks for canonical media merges.

SQLite has a single writer, so lost updates between overlapping merges and alert
evaluation only show up on PostgreSQL. These tests run when
``PODEX_DATABASE__URL`` points at PostgreSQL and are skipped otherwise. Each test
builds the schema in a throwaway PostgreSQL schema and drops it afterwards:

    PODEX_DATABASE__URL=postgresql+psycopg://podex@localhost:5432/podex \\
    uv run pytest tests/test_media_merge_postgres.py
"""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial

import pytest
from assertpy import assert_that
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from podex.models import (
    AccountAlertRule,
    AccountSavedMedia,
    AccountUser,
    Base,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
)
from podex.services.account_alerts import evaluate_alert_rules
from podex.services.ops_media_commands import merge_ops_media
from tests.conftest import create_media_alert_rule

_DATABASE_URL = os.environ.get("PODEX_DATABASE__URL", "")
_LOCK_WAIT_TIMEOUT_SECONDS = 10.0

pytestmark = pytest.mark.skipif(
    not _DATABASE_URL.startswith("postgresql"),
    reason="PODEX_DATABASE__URL does not point at PostgreSQL",
)

Step = Callable[[Session], list[tuple[int, int]]]


@dataclass(frozen=True, slots=True)
class _PostgresDatabase:
    """Engine and session factory bound to a throwaway PostgreSQL schema."""

    engine: Engine
    sessions: sessionmaker[Session]


@dataclass(frozen=True, slots=True)
class _MergeFixture:
    """Identifiers of the seeded user, episode and media records."""

    user_id: int
    episode_id: int
    target_id: int
    source_ids: tuple[int, ...]


@pytest.fixture(name="postgres")
def postgres_fixture() -> Iterator[_PostgresDatabase]:
    """Create every model table in a fresh schema and drop it afterwards.

    Yields:
        Engine and session factory whose search path is the fresh schema.
    """
    schema = f"merge_concurrency_{uuid.uuid4().hex[:12]}"
    admin = create_engine(_DATABASE_URL)
    with admin.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        _DATABASE_URL,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    try:
        Base.metadata.create_all(engine)
        yield _PostgresDatabase(
            engine=engine,
            sessions=sessionmaker(bind=engine, expire_on_commit=False),
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def _seed(
    db: Session,
    *,
    source_count: int,
    rule_media: str,
    unseen_target_mentions: int = 0,
) -> _MergeFixture:
    """Persist a target and sources with one mention each and baselined rules.

    Args:
        db: Session to seed through; committed before returning.
        source_count: Number of same-type source records to create.
        rule_media: ``"all"`` for a rule on every record, ``"target"`` for one
            on the target only.
        unseen_target_mentions: Target mentions added after the rules baselined.

    Returns:
        Identifiers of the seeded rows.
    """
    podcast = Podcast(name="Merge Show", slug="merge-show")
    db.add(podcast)
    db.flush()
    episode = Episode(podcast_id=podcast.id, title="Merge Episode")
    user = AccountUser(email="merge-concurrency@example.com")
    target = Media(type=MediaType.BOOK, title="Dune")
    sources = [
        Media(type=MediaType.BOOK, title=f"Dune (dupe {index})")
        for index in range(source_count)
    ]
    db.add_all([episode, user, target, *sources])
    db.flush()
    for media in (target, *sources):
        db.add(Mention(episode_id=episode.id, media_id=media.id))
        db.add(AccountSavedMedia(user_id=user.id, media_id=media.id))
    db.flush()
    watched = (target, *sources) if rule_media == "all" else (target,)
    for media in watched:
        create_media_alert_rule(db=db, user_id=user.id, media_id=media.id)
    db.add_all(
        Mention(episode_id=episode.id, media_id=target.id)
        for _ in range(unseen_target_mentions)
    )
    db.commit()
    return _MergeFixture(
        user_id=user.id,
        episode_id=episode.id,
        target_id=target.id,
        source_ids=tuple(source.id for source in sources),
    )


def _merge(db: Session, *, source_id: int, target_id: int) -> list[tuple[int, int]]:
    """Merge one source into the target without committing.

    Returns:
        An empty list; merges generate no alert events.
    """
    merge_ops_media(db=db, source_media_id=source_id, target_media_id=target_id)
    return []


def _evaluate(db: Session, *, user_id: int) -> list[tuple[int, int]]:
    """Evaluate a user's alert rules without committing.

    Returns:
        ``(previous_count, observed_count)`` for each generated event.
    """
    return [
        (item.event.previous_count, item.event.observed_count)
        for item in evaluate_alert_rules(db=db, user_id=user_id)
    ]


def _run_then_commit(db: Session, step: Step) -> list[tuple[int, int]]:
    """Run a step and commit it, as a request or scheduler transaction would.

    Returns:
        The step's result.
    """
    result = step(db)
    db.commit()
    return result


def _wait_until_lock_wait(engine: Engine, backend_pid: int) -> None:
    """Block until the backend with ``backend_pid`` waits on a lock.

    Raises:
        AssertionError: If the backend never waits on a lock.
    """
    deadline = time.monotonic() + _LOCK_WAIT_TIMEOUT_SECONDS
    query = text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid")
    while time.monotonic() < deadline:
        # pg_stat_activity is a per-transaction snapshot, so poll in new ones.
        with engine.connect() as connection:
            wait_type = connection.execute(query, {"pid": backend_pid}).scalar()
        if wait_type == "Lock":
            return
        time.sleep(0.02)
    raise AssertionError("the second transaction never waited on a lock")


def _run_overlapping(
    postgres: _PostgresDatabase,
    *,
    first: Step,
    second: Step,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Hold ``first`` open, start ``second`` until it blocks, then commit both.

    Returns:
        The first and second steps' results.
    """
    first_db = postgres.sessions()
    second_db = postgres.sessions()
    second_pid = int(second_db.execute(text("SELECT pg_backend_pid()")).scalar_one())
    try:
        first_result = first(first_db)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run_then_commit, second_db, second)
            try:
                _wait_until_lock_wait(postgres.engine, second_pid)
            finally:
                first_db.commit()
            return first_result, future.result(timeout=_LOCK_WAIT_TIMEOUT_SECONDS)
    finally:
        first_db.close()
        second_db.close()


def _assert_settled(postgres: _PostgresDatabase, fixture: _MergeFixture) -> None:
    """One survivor rule has seen every mention and evaluates to no event."""
    with postgres.sessions() as db:
        rules = db.query(AccountAlertRule).all()
        mention_count = db.query(Mention).count()
        assert_that(
            [(rule.target_id, rule.baseline_count) for rule in rules],
        ).is_equal_to([(fixture.target_id, mention_count)])
        assert_that(_evaluate(db, user_id=fixture.user_id)).is_empty()
        assert_that(db.query(Media).count()).is_equal_to(1)


def test_concurrent_merges_into_one_target_keep_every_baseline(
    postgres: _PostgresDatabase,
) -> None:
    """Two merges into one survivor serialize and count all seen mentions."""
    with postgres.sessions() as db:
        fixture = _seed(db, source_count=2, rule_media="all")
    first_source, second_source = fixture.source_ids

    _run_overlapping(
        postgres,
        first=partial(_merge, source_id=first_source, target_id=fixture.target_id),
        second=partial(_merge, source_id=second_source, target_id=fixture.target_id),
    )

    _assert_settled(postgres, fixture)
    with postgres.sessions() as db:
        assert_that(db.query(Mention).count()).is_equal_to(3)


@pytest.mark.parametrize(
    ("merge_first", "expected_events"),
    [(True, [(2, 3)]), (False, [(1, 2)])],
    ids=["merge-first", "evaluation-first"],
)
def test_merge_and_alert_evaluation_do_not_interleave(
    postgres: _PostgresDatabase,
    merge_first: bool,
    expected_events: list[tuple[int, int]],
) -> None:
    """Evaluation never writes back a baseline read before a merge committed."""
    with postgres.sessions() as db:
        fixture = _seed(
            db,
            source_count=1,
            rule_media="target",
            unseen_target_mentions=1,
        )
    merge = partial(
        _merge,
        source_id=fixture.source_ids[0],
        target_id=fixture.target_id,
    )
    evaluate = partial(_evaluate, user_id=fixture.user_id)

    if merge_first:
        _, generated = _run_overlapping(postgres, first=merge, second=evaluate)
    else:
        generated, _ = _run_overlapping(postgres, first=evaluate, second=merge)

    assert_that(generated).is_equal_to(expected_events)
    _assert_settled(postgres, fixture)
