"""Application behaviour that only real PostgreSQL can prove.

Each test runs in its own database migrated with ``alembic upgrade head`` (the
shared ``postgres`` fixture), so these check the migrated schema rather than
``Base.metadata``. They run in the PostgreSQL CI lane and are skipped elsewhere.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg.errors
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.config import RateLimitSettings, Settings
from podex.database import get_db
from podex.main import create_app
from podex.models import (
    Episode,
    IngestionRun,
    IngestionRunStatus,
    Media,
    MediaType,
    Podcast,
    ScheduledWorkItemModel,
)
from podex.services.discovery import DiscoveryResult
from podex.services.recurring_discovery import (
    plan_recurring_discovery_work,
    run_due_episode_discovery_work,
)
from tests.postgres.concurrency import run_overlapping
from tests.postgres.conftest import ALEMBIC_INI, PostgresDatabase

pytestmark = pytest.mark.postgres


def _add_podcast(db: Session, *, slug: str) -> None:
    """Add and flush a podcast with ``slug`` without committing."""
    db.add(Podcast(name=f"Show {slug}", slug=slug))
    db.flush()


def test_fixture_database_is_migrated_to_head_with_pgvector(
    postgres: PostgresDatabase,
) -> None:
    """The per-test database is the Alembic head schema, pgvector included."""
    head = ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    with postgres.engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version"),
        ).scalar_one()
        extension = connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'"),
        ).scalar_one_or_none()
        embedding_type = connection.execute(
            text(
                "SELECT udt_name FROM information_schema.columns"
                " WHERE table_name = 'semantic_chunks'"
                " AND column_name = 'embedding_vector'",
            ),
        ).scalar_one()

    assert_that(revision).is_equal_to(head)
    assert_that(extension).is_equal_to("vector")
    assert_that(embedding_type).is_equal_to("vector")


def test_enums_and_aware_timestamps_round_trip(postgres: PostgresDatabase) -> None:
    """Enums persist by name; aware timestamps return the same instant, aware."""
    started_at = datetime(2026, 5, 10, 12, 30, tzinfo=UTC)
    with postgres.sessions() as db:
        db.add(Media(type=MediaType.TV_SHOW, title="The Wire"))
        db.add(IngestionRun(status=IngestionRunStatus.COMPLETED, started_at=started_at))
        db.commit()

    with postgres.sessions() as db:
        # A non-UTC session zone: the value must still be the same instant.
        db.execute(text("SET TIME ZONE 'Asia/Kolkata'"))
        stored = db.execute(text("SELECT type FROM media")).scalar_one()
        stored_status = db.execute(
            text("SELECT status FROM ingestion_runs")
        ).scalar_one()
        column_type = db.execute(
            text(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name = 'ingestion_runs'"
                " AND column_name = 'started_at'",
            ),
        ).scalar_one()
        media = db.query(Media).one()
        run = db.query(IngestionRun).one()

    assert_that(stored).is_equal_to(MediaType.TV_SHOW.name)
    assert_that(stored_status).is_equal_to(IngestionRunStatus.COMPLETED.name)
    assert_that(media.type).is_same_as(MediaType.TV_SHOW)
    assert_that(run.status).is_same_as(IngestionRunStatus.COMPLETED)
    assert_that(column_type).is_equal_to("timestamp with time zone")
    assert_that(run.started_at).is_equal_to(started_at)
    assert_that(run.started_at.utcoffset()).is_equal_to(
        timedelta(hours=5, minutes=30),
    )


def test_foreign_key_violation_rolls_back_the_whole_transaction(
    postgres: PostgresDatabase,
) -> None:
    """A dangling foreign key is rejected and its transaction leaves no rows."""
    with postgres.sessions() as db:
        _add_podcast(db, slug="orphan-parent")
        db.add(Episode(podcast_id=999_999, title="Orphan"))
        with pytest.raises(IntegrityError) as raised:
            db.flush()
        db.rollback()

    with postgres.sessions() as db:
        assert_that(raised.value.orig).is_instance_of(
            psycopg.errors.ForeignKeyViolation,
        )
        assert_that(db.query(Podcast).count()).is_zero()
        assert_that(db.query(Episode).count()).is_zero()


def test_concurrent_unique_insert_waits_then_conflicts(
    postgres: PostgresDatabase,
) -> None:
    """A racing duplicate slug blocks on the first insert, then is rejected."""
    with pytest.raises(IntegrityError) as raised:
        run_overlapping(
            postgres,
            first=lambda db: _add_podcast(db, slug="race"),
            second=lambda db: _add_podcast(db, slug="race"),
        )

    assert_that(raised.value.orig).is_instance_of(psycopg.errors.UniqueViolation)
    with postgres.sessions() as db:
        assert_that(db.query(Podcast.slug).all()).is_equal_to([("race",)])


def test_discovery_workers_skip_work_claimed_by_another_worker(
    postgres: PostgresDatabase,
) -> None:
    """``FOR UPDATE SKIP LOCKED`` gives one due item to exactly one worker."""
    with postgres.sessions() as db:
        db.add(Podcast(name="Claimed", slug="claimed", status="active"))
        db.flush()
        planned = plan_recurring_discovery_work(db=db, now=datetime.now(UTC))
        db.commit()
    assert_that(planned).is_length(1)
    runs: list[str] = []

    def runner(_db: Session, podcast: Podcast) -> DiscoveryResult:
        runs.append(podcast.slug)
        return DiscoveryResult(new_episodes=1, total_discovered=1)

    first_worker = postgres.sessions()
    second_worker = postgres.sessions()
    try:
        claimed = run_due_episode_discovery_work(db=first_worker, runner=runner)
        # The first worker's claim is uncommitted and still locks the row. A
        # plain FOR UPDATE would wait; the timeout turns that into a failure.
        second_worker.execute(text("SET LOCAL lock_timeout = '5s'"))
        skipped = run_due_episode_discovery_work(db=second_worker, runner=runner)
        first_worker.commit()
        second_worker.commit()
    finally:
        first_worker.close()
        second_worker.close()

    assert_that(claimed).is_length(1)
    assert_that(skipped).is_empty()
    assert_that(runs).is_equal_to(["claimed"])
    with postgres.sessions() as db:
        assert_that(db.query(ScheduledWorkItemModel.status).all()).is_equal_to(
            [("completed",)],
        )
        assert_that(db.query(IngestionRun).count()).is_equal_to(1)


def test_api_serves_rows_from_migrated_postgres(postgres: PostgresDatabase) -> None:
    """The public API reads from the PostgreSQL database end to end."""
    with postgres.sessions() as db:
        _add_podcast(db, slug="pg-show")
        db.commit()
    app = create_app(Settings(rate_limit=RateLimitSettings(enabled=False)))

    def override_get_db() -> Iterator[Session]:
        """Yield a session on the test's PostgreSQL database."""
        with postgres.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        response = client.get("/api/v2/podcasts")

    assert_that(response.status_code).is_equal_to(200)
    assert_that([item["slug"] for item in response.json()["items"]]).is_equal_to(
        ["pg-show"],
    )
