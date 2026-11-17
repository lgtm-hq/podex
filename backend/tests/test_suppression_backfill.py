"""The suppression backfill repairs a rolling deploy and is safe to re-run.

An instance still running the previous code approves a takedown against the
new schema: the content is removed, but no suppression is recorded. Running
the backfill again after the rollout records it; running it once more writes
nothing. New code against the old schema fails closed.
"""

from pathlib import Path

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from podex import suppression_backfill
from podex.models import (
    Base,
    ContentSuppression,
    Mention,
    TakedownSubjectType,
)
from podex.services.content_suppression import suppression_version
from tests.conftest import seed_catalog_graph
from tests.takedown_support import approve_takedown, take_down_episode


def _executed_without_suppression(db: Session) -> int:
    """Approve an episode takedown, then drop its row, as previous code left it.

    Returns:
        The taken-down episode id.
    """
    graph = seed_catalog_graph(db)
    take_down_episode(db, graph.episode_id, "unpublish_mentions")
    db.query(ContentSuppression).delete()
    db.commit()
    return graph.episode_id


def test_rerun_after_rollout_records_the_missing_suppression(
    db_session: Session,
) -> None:
    """The re-run writes exactly the missing row and bumps the version."""
    episode_id = _executed_without_suppression(db_session)
    before = suppression_version(db_session)

    report = suppression_backfill.backfill_suppressions(db_session.connection())
    db_session.commit()

    assert_that(report.written).is_length(1)
    assert_that(report.written).is_equal_to([f"episode:{episode_id}:mentions"])
    assert_that(db_session.query(ContentSuppression).count()).is_equal_to(1)
    assert_that(suppression_version(db_session)).is_greater_than(before)


def test_rerun_when_nothing_is_missing_writes_nothing(db_session: Session) -> None:
    """A second run, and a run after a complete takedown, are no-ops."""
    graph = seed_catalog_graph(db_session)
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=graph.episode_id,
        actions=["unpublish_mentions", "suppress_derivatives"],
    )
    before = suppression_version(db_session)

    first = suppression_backfill.backfill_suppressions(db_session.connection())
    second = suppression_backfill.backfill_suppressions(db_session.connection())
    db_session.commit()

    assert_that(first.written).is_empty()
    assert_that(second.written).is_empty()
    assert_that(db_session.query(ContentSuppression).count()).is_equal_to(2)
    assert_that(suppression_version(db_session)).is_equal_to(before)


def test_dry_run_reports_without_writing(db_session: Session) -> None:
    """A dry run names the missing row and writes nothing."""
    _executed_without_suppression(db_session)

    report = suppression_backfill.backfill_suppressions(
        db_session.connection(),
        dry_run=True,
    )
    db_session.commit()

    assert_that(report.written).is_length(1)
    assert_that(db_session.query(ContentSuppression).count()).is_zero()


def test_command_writes_and_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``python -m podex.suppression_backfill`` runs against the configured database."""
    engine = create_engine(f"sqlite:///{tmp_path / 'command.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        _executed_without_suppression(db)
    monkeypatch.setattr(suppression_backfill, "build_engine", lambda _settings: engine)

    dry_status = suppression_backfill.main(["--dry-run"])
    dry_output = capsys.readouterr().out
    status = suppression_backfill.main([])
    output = capsys.readouterr().out

    assert_that(dry_status).is_zero()
    assert_that(dry_output).contains("would write 1 suppressions")
    assert_that(status).is_zero()
    assert_that(output).contains("wrote 1 suppressions")
    with Session(engine) as db:
        assert_that(db.query(ContentSuppression).count()).is_equal_to(1)
    engine.dispose()


def test_new_code_on_the_old_schema_fails_closed(
    client: TestClient,
    db_session: Session,
) -> None:
    """Without the suppression table, mention reads answer 500, not content."""
    graph = seed_catalog_graph(db_session)
    engine = db_session.get_bind()
    Base.metadata.tables["content_suppressions"].drop(engine)
    db_session.commit()
    failing = TestClient(client.app, raise_server_exceptions=False)

    for path in (
        f"/api/v2/episodes/{graph.episode_id}/mentions",
        f"/api/v2/media/{graph.media_id}/mentions",
        "/api/v2/stats",
    ):
        response = failing.get(path)
        assert_that(response.status_code).described_as(path).is_equal_to(500)
        assert_that(response.text).described_as(path).does_not_contain("a great book")
    assert_that(db_session.query(Mention).count()).is_equal_to(1)
