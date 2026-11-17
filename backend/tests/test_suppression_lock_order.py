"""Writers and takedowns take their locks media rows first, then podcast rows.

SQLite ignores the lock clauses but still runs the locking statements, so
their order is visible here; the PostgreSQL lane proves the waits themselves.
"""

from collections.abc import Callable
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from podex.models import Episode, TakedownSubjectType
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.derivative_summaries import GeneratedSummaryData
from tests.conftest import seed_catalog_graph
from tests.takedown_support import approve_takedown, recorded_statements

_MEDIA_LOCK = "FROM media \nWHERE media.id IN"
_PODCAST_LOCK = "SELECT podcasts.id"


def _lock_kinds(statements: list[str]) -> list[str]:
    """Classify the media and podcast lock statements, in order."""
    kinds: list[str] = []
    for statement in statements:
        if statement.startswith("SELECT media.id") and _MEDIA_LOCK in statement:
            kinds.append("media")
        elif statement.startswith(_PODCAST_LOCK):
            kinds.append("podcast")
    return kinds


def _first_locks(recorded: list[str]) -> list[str]:
    """Collapse repeats: the order in which each kind of lock first appears."""
    order: list[str] = []
    for kind in recorded:
        if kind not in order:
            order.append(kind)
    return order


def _generate(db: Session, episode_id: int) -> None:
    provider = Mock(model_name="model")
    provider.summarize.return_value = GeneratedSummaryData(summary_text="summary")
    generate_episode_derivatives(
        db=db,
        episode=db.query(Episode).filter(Episode.id == episode_id).one(),
        summary_provider=provider,
    )


def _take_down(db: Session, episode_id: int) -> None:
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=episode_id,
        actions=["suppress_derivatives"],
    )


@pytest.mark.parametrize(
    "operation",
    [_generate, _take_down],
    ids=["derivative-generation", "takedown"],
)
def test_media_rows_are_locked_before_podcast_rows(
    db_session: Session,
    operation: Callable[[Session, int], None],
) -> None:
    """The first media lock comes before the first podcast lock."""
    graph = seed_catalog_graph(db_session)
    engine = db_session.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))

    with recorded_statements(engine) as statements:
        operation(db_session, graph.episode_id)

    assert_that(_first_locks(_lock_kinds(statements))).is_equal_to(
        ["media", "podcast"],
    )
