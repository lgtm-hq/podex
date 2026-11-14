"""PostgreSQL concurrency checks for canonical media merges.

SQLite has a single writer, so lost updates between overlapping merges and alert
evaluation only show up on PostgreSQL. Each test runs in its own migrated
database from the shared ``postgres`` fixture (``tests/postgres/conftest.py``)
and runs in the PostgreSQL CI lane; elsewhere the ``postgres`` marker skips it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Query, Session

from podex.models import (
    AccountAlertRule,
    AccountSavedMedia,
    AccountUser,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
)
from podex.services.account_alerts import evaluate_alert_rules
from podex.services.ops_media_commands import merge_ops_media
from tests.conftest import create_media_alert_rule
from tests.postgres.concurrency import row_lock_wait, run_overlapping
from tests.postgres.conftest import PostgresDatabase

pytestmark = pytest.mark.postgres


@dataclass(frozen=True, slots=True)
class _MergeFixture:
    """Identifiers of the seeded user, episode and media records."""

    user_id: int
    episode_id: int
    target_id: int
    source_ids: tuple[int, ...]


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


def _assert_settled(postgres: PostgresDatabase, fixture: _MergeFixture) -> None:
    """One survivor rule has seen every mention and evaluates to no event."""
    with postgres.sessions() as db:
        rules = db.query(AccountAlertRule).all()
        mention_count = db.query(Mention).count()
        assert_that(
            [(rule.target_id, rule.baseline_count) for rule in rules],
        ).is_equal_to([(fixture.target_id, mention_count)])
        assert_that(_evaluate(db, user_id=fixture.user_id)).is_empty()
        assert_that(db.query(Media).count()).is_equal_to(1)


def _concurrent_merges(postgres: PostgresDatabase) -> _MergeFixture:
    """Overlap two merges into one survivor; the second waits on its row.

    Returns:
        The seeded identifiers.
    """
    with postgres.sessions() as db:
        fixture = _seed(db, source_count=2, rule_media="all")
    first_source, second_source = fixture.source_ids

    run_overlapping(
        postgres,
        first=partial(_merge, source_id=first_source, target_id=fixture.target_id),
        second=partial(_merge, source_id=second_source, target_id=fixture.target_id),
        # lock_merge_pair locks the survivor's media row first in both merges.
        expected=row_lock_wait("media"),
    )
    return fixture


def _merge_and_evaluation(
    postgres: PostgresDatabase,
    *,
    merge_first: bool,
) -> tuple[_MergeFixture, list[tuple[int, int]]]:
    """Overlap a merge and alert evaluation on the survivor's alert rule.

    Returns:
        The seeded identifiers and the events evaluation generated.
    """
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
    # Whichever runs second must wait in its own FOR UPDATE on the rule rows
    # (evaluation's _lock_alert_rules, or the merge's rule plan), not at a
    # later write.
    rule_lock = row_lock_wait("account_alert_rules")

    if merge_first:
        _, generated = run_overlapping(
            postgres, first=merge, second=evaluate, expected=rule_lock
        )
    else:
        generated, _ = run_overlapping(
            postgres, first=evaluate, second=merge, expected=rule_lock
        )
    return fixture, generated


def test_concurrent_merges_into_one_target_keep_every_baseline(
    postgres: PostgresDatabase,
) -> None:
    """Two merges into one survivor serialize and count all seen mentions."""
    fixture = _concurrent_merges(postgres)

    _assert_settled(postgres, fixture)
    with postgres.sessions() as db:
        assert_that(db.query(Mention).count()).is_equal_to(3)


@pytest.mark.parametrize(
    ("merge_first", "expected_events"),
    [(True, [(2, 3)]), (False, [(1, 2)])],
    ids=["merge-first", "evaluation-first"],
)
def test_merge_and_alert_evaluation_do_not_interleave(
    postgres: PostgresDatabase,
    merge_first: bool,
    expected_events: list[tuple[int, int]],
) -> None:
    """Evaluation never writes back a baseline read before a merge committed."""
    fixture, generated = _merge_and_evaluation(postgres, merge_first=merge_first)

    assert_that(generated).is_equal_to(expected_events)
    _assert_settled(postgres, fixture)


def _without_row_lock(model: type[Any]) -> Callable[..., Query[Any]]:
    """Return a ``Query.with_for_update`` that skips the lock for ``model``.

    A test-only mutation: queries on ``model`` run without ``FOR UPDATE``;
    every other query keeps its lock.
    """
    locked = Query.with_for_update

    def with_for_update(self: Query[Any], *args: Any, **kwargs: Any) -> Query[Any]:
        if self.column_descriptions[0]["entity"] is model:
            return self
        return locked(self, *args, **kwargs)

    return with_for_update


@pytest.mark.parametrize(
    ("scenario", "unlocked_model"),
    [
        (_concurrent_merges, Media),
        (partial(_merge_and_evaluation, merge_first=True), AccountAlertRule),
        (partial(_merge_and_evaluation, merge_first=False), AccountAlertRule),
    ],
    ids=["concurrent-merges", "merge-first", "evaluation-first"],
)
def test_scenario_fails_without_its_production_row_lock(
    postgres: PostgresDatabase,
    monkeypatch: pytest.MonkeyPatch,
    scenario: Callable[[PostgresDatabase], object],
    unlocked_model: type[Any],
) -> None:
    """Removing the row lock a scenario relies on makes the scenario fail.

    Without ``lock_merge_pair``'s media lock, or the alert-rule locks, the
    second transaction no longer waits in that ``FOR UPDATE``; at most it
    waits at a later statement, which the race helper refuses.
    """
    monkeypatch.setattr(Query, "with_for_update", _without_row_lock(unlocked_model))

    with pytest.raises(AssertionError, match="never waited on the expected lock"):
        scenario(postgres)
