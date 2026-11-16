"""Tests for the review item decision methods.

The methods record each decision; they do not guard illegal transitions,
which belong to the review decision service, so these tests start every
method from a pending item.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.orm import Session

from podex.models import Media, MediaType, MentionCandidate, ReviewItem
from podex.models import review_item as review_item_module
from podex.models.review_item import ReviewItemStatus
from tests.conftest import SeededGraph

_CREATED = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
_DECIDED = _CREATED + timedelta(hours=2)

Decision = Callable[[ReviewItem], None]


@dataclass
class _Clock:
    """Stand-in for ``datetime`` whose ``now`` returns a settable instant."""

    current: datetime

    def now(self, tz: object) -> datetime:
        """Return the current instant; the module always asks for UTC."""
        assert_that(tz).is_same_as(UTC)
        return self.current


@pytest.fixture(name="clock")
def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Freeze the review item module's clock at the creation instant."""
    clock = _Clock(current=_CREATED)
    monkeypatch.setattr(review_item_module, "datetime", clock)
    return clock


def _pending_item(
    db: Session,
    graph: SeededGraph,
    *,
    assigned_to: str | None = None,
) -> ReviewItem:
    """Persist a pending review item for a fresh mention candidate."""
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
    )
    db.add(candidate)
    db.flush()
    item = ReviewItem(
        mention_candidate_id=candidate.id,
        target_media_id=graph.media_id,
        assigned_to=assigned_to,
    )
    db.add(item)
    db.commit()
    return item


def _stored(db: Session, item_id: int) -> ReviewItem:
    """Reload the item from the database rather than the identity map."""
    db.expire_all()
    return db.execute(select(ReviewItem).where(ReviewItem.id == item_id)).scalar_one()


def test_assign_moves_item_into_review_without_deciding(
    db_session: Session,
    seeded_graph: SeededGraph,
    clock: _Clock,
) -> None:
    """Assigning sets the reviewer and touches the item but records no decision."""
    item = _pending_item(db_session, seeded_graph)
    clock.current = _DECIDED

    item.assign(actor_name="ops-reviewer")

    assert_that(item.status).is_equal_to(ReviewItemStatus.IN_REVIEW.value)
    assert_that(item.assigned_to).is_equal_to("ops-reviewer")
    assert_that(item.updated_at).is_equal_to(_DECIDED)
    assert_that(item.decided_at).is_none()
    assert_that(item.decision_note).is_none()
    db_session.commit()
    stored = _stored(db_session, item.id)
    assert_that(stored.status).is_equal_to(ReviewItemStatus.IN_REVIEW.value)
    assert_that(stored.assigned_to).is_equal_to("ops-reviewer")
    assert_that(stored.decided_at).is_none()


def test_assign_without_actor_keeps_existing_reviewer(
    db_session: Session,
    seeded_graph: SeededGraph,
    clock: _Clock,
) -> None:
    """An anonymous assignment does not clear the reviewer already attached."""
    item = _pending_item(db_session, seeded_graph, assigned_to="first-reviewer")
    clock.current = _DECIDED

    item.assign()

    assert_that(item.status).is_equal_to(ReviewItemStatus.IN_REVIEW.value)
    assert_that(item.assigned_to).is_equal_to("first-reviewer")
    assert_that(item.updated_at).is_equal_to(_DECIDED)


@pytest.mark.parametrize(
    ("decide", "expected_status"),
    [
        (
            lambda item: item.approve(actor_name="ops-reviewer", note="matches"),
            ReviewItemStatus.APPROVED,
        ),
        (
            lambda item: item.reject(actor_name="ops-reviewer", note="matches"),
            ReviewItemStatus.REJECTED,
        ),
        (
            lambda item: item.mark_split(actor_name="ops-reviewer", note="matches"),
            ReviewItemStatus.SPLIT,
        ),
    ],
    ids=["approve", "reject", "split"],
)
def test_decisions_record_status_reviewer_note_and_time(
    db_session: Session,
    seeded_graph: SeededGraph,
    clock: _Clock,
    decide: Decision,
    expected_status: ReviewItemStatus,
) -> None:
    """Each decision stamps one instant on updated_at and decided_at."""
    item = _pending_item(db_session, seeded_graph)
    clock.current = _DECIDED

    decide(item)

    assert_that(item.status).is_equal_to(expected_status.value)
    assert_that(item.assigned_to).is_equal_to("ops-reviewer")
    assert_that(item.decision_note).is_equal_to("matches")
    assert_that(item.updated_at).is_equal_to(_DECIDED)
    assert_that(item.decided_at).is_equal_to(_DECIDED)
    assert_that(item.target_media_id).is_equal_to(seeded_graph.media_id)
    db_session.commit()
    stored = _stored(db_session, item.id)
    assert_that(stored.status).is_equal_to(expected_status.value)
    assert_that(stored.decision_note).is_equal_to("matches")
    assert_that(stored.decided_at).is_not_none()
    assert_that(stored.target_media_id).is_equal_to(seeded_graph.media_id)


@pytest.mark.parametrize(
    "decide",
    [
        lambda item: item.approve(),
        lambda item: item.reject(),
        lambda item: item.mark_split(),
        lambda item: item.mark_merged(target_media_id=item.target_media_id or 0),
    ],
    ids=["approve", "reject", "split", "merge"],
)
def test_anonymous_decision_keeps_reviewer_and_clears_note(
    db_session: Session,
    seeded_graph: SeededGraph,
    clock: _Clock,
    decide: Decision,
) -> None:
    """Without an actor the reviewer stays; without a note the old note is cleared."""
    item = _pending_item(db_session, seeded_graph, assigned_to="first-reviewer")
    item.decision_note = "earlier note"
    db_session.commit()
    clock.current = _DECIDED

    decide(item)

    assert_that(item.assigned_to).is_equal_to("first-reviewer")
    assert_that(item.decision_note).is_none()
    assert_that(item.decided_at).is_equal_to(_DECIDED)


def test_mark_merged_records_the_canonical_target(
    db_session: Session,
    seeded_graph: SeededGraph,
    clock: _Clock,
) -> None:
    """Merging points the item at the media record it was merged into."""
    item = _pending_item(db_session, seeded_graph)
    canonical = Media(type=MediaType.BOOK, title="Dune Messiah", author="Herbert")
    db_session.add(canonical)
    db_session.commit()
    clock.current = _DECIDED

    item.mark_merged(
        actor_name="ops-reviewer",
        note="same work",
        target_media_id=canonical.id,
    )

    assert_that(item.status).is_equal_to(ReviewItemStatus.MERGED.value)
    assert_that(item.assigned_to).is_equal_to("ops-reviewer")
    assert_that(item.decision_note).is_equal_to("same work")
    assert_that(item.target_media_id).is_equal_to(canonical.id)
    assert_that(item.updated_at).is_equal_to(_DECIDED)
    assert_that(item.decided_at).is_equal_to(_DECIDED)
    db_session.commit()
    stored = _stored(db_session, item.id)
    assert_that(stored.status).is_equal_to(ReviewItemStatus.MERGED.value)
    assert_that(stored.target_media_id).is_equal_to(canonical.id)
    assert_that(stored.target_media).is_not_none()
    assert_that(stored.target_media.title if stored.target_media else "").is_equal_to(
        "Dune Messiah",
    )
