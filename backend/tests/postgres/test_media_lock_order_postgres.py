"""PostgreSQL races between a media merge or split and the writers it meets.

Every writer that touches rows referencing a media record takes the parent
media rows first (ascending id), then podcast rows, then alert rules by
``(user_id, id)``, then their events and saves; the suppression change counter
is always last (ADR 0020). Each test pauses one transaction inside its work
(``run_paused``), runs the other against it, then lets the first finish, in
both orders. Each asserts:

- no deadlock is reported, and a side that fails does so cleanly;
- the side that came second waited on the media row;
- no alert rule, save or mention points at a deleted media record;
- the next alert evaluation reports exactly the expected new mentions.

Steps run with autoflush off, as the application's sessions do.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial
from typing import TypeVar

import pytest
from assertpy import assert_that
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountSavedMedia,
    AccountUser,
    ContentSuppression,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
    TakedownSubjectType,
)
from podex.services.account_alerts import delete_alert_rule, evaluate_alert_rules
from podex.services.account_privacy import delete_account
from podex.services.account_saves import remove_saved_media, save_media
from podex.services.content_suppression import RetryableLockError, suppression_version
from podex.services.ops_media_commands import (
    SplitOpsMediaInputData,
    merge_ops_media,
    split_ops_media,
)
from tests.conftest import create_media_alert_rule
from tests.postgres.concurrency import StepOutcome, run_paused
from tests.postgres.conftest import PostgresDatabase
from tests.takedown_support import execute_takedown

pytestmark = pytest.mark.postgres

_Result = TypeVar("_Result")

# The merge pauses once it has locked its media rows and alert rules and read
# the first side's mention count, before it writes anything it planned.
_MERGE_PAUSE = r"^SELECT count\(\*\) AS count_1\s+FROM mentions\b"
_WRITER_WAITS_ON_MEDIA = r"(?s)\bFROM media\b.*\bFOR KEY SHARE\b"
_MERGE_WAITS_ON_MEDIA = r"(?s)\bFROM media\b.*\bFOR UPDATE\b"
_DEADLOCK_SQLSTATE = "40P01"

ORDERS = pytest.mark.parametrize(
    "merge_first",
    [True, False],
    ids=["merge-first", "writer-first"],
)
RECORDS = pytest.mark.parametrize("record", ["source", "target"])


@dataclass(frozen=True, slots=True)
class _Catalog:
    """Seeded rows: a merge of ``source_id`` into ``target_id`` is raced.

    The owner saves both records and has one baselined rule on each; each
    record has one mention, in episodes 1 (target) and 2 (source).
    """

    episode_ids: tuple[int, int, int]
    target_id: int
    source_id: int
    target_mention_id: int
    source_mention_id: int
    owner_id: int
    source_rule_id: int

    def media_id(self, record: str) -> int:
        """Return the id of ``"source"`` or ``"target"``."""
        return self.source_id if record == "source" else self.target_id


@dataclass(frozen=True, slots=True)
class _Race:
    """Outcomes of one merge raced against one writer."""

    merge: StepOutcome
    writer: StepOutcome
    waited_on: str | None
    expected_wait: str

    def assert_clean(self) -> None:
        """Neither side reported a deadlock."""
        for outcome in (self.merge, self.writer):
            _assert_not_deadlocked(outcome)

    def assert_waited_on_media(self) -> None:
        """The side that ran second waited on the media lock."""
        assert_that(self.waited_on).described_as("second side's wait").is_not_none()
        assert_that(self.waited_on).matches(self.expected_wait)


def _assert_not_deadlocked(outcome: StepOutcome) -> None:
    """Fail when the error, or anything it was raised from, is a deadlock."""
    error: BaseException | None = outcome.error
    while error is not None:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        assert_that(sqlstate).described_as(f"deadlock: {error!r}").is_not_equal_to(
            _DEADLOCK_SQLSTATE,
        )
        error = error.__cause__ or error.__context__


def _production(step: Callable[[Session], _Result]) -> Callable[[Session], _Result]:
    """Run ``step`` with autoflush off, as ``SessionLocal`` sessions do."""

    def run(db: Session) -> _Result:
        db.autoflush = False
        return step(db)

    return run


def _user(db: Session, email: str, *, saves: tuple[int, ...] = ()) -> int:
    """Persist an account that saved ``saves``.

    Returns:
        The account id.
    """
    user = AccountUser(email=email)
    db.add(user)
    db.flush()
    db.add_all(AccountSavedMedia(user_id=user.id, media_id=media) for media in saves)
    db.flush()
    user_id: int = user.id
    return user_id


def _seed(postgres: PostgresDatabase) -> _Catalog:
    """Seed the merge pair, its mentions, the owner's saves and rules.

    Returns:
        The seeded identifiers.
    """
    with postgres.sessions() as db:
        podcast = Podcast(name="Lock Order Show", slug="lock-order-show")
        db.add(podcast)
        db.flush()
        episodes = [
            Episode(podcast_id=podcast.id, title=f"Episode {number}")
            for number in (1, 2, 3)
        ]
        target = Media(type=MediaType.BOOK, title="Dune")
        db.add_all([*episodes, target])
        db.flush()
        # Created after the target, so the target has the lower id.
        source = Media(type=MediaType.BOOK, title="Dune (duplicate)")
        db.add(source)
        db.flush()
        target_mention = Mention(episode_id=episodes[0].id, media_id=target.id)
        source_mention = Mention(episode_id=episodes[1].id, media_id=source.id)
        db.add_all([target_mention, source_mention])
        db.flush()
        owner_id = _user(db, "owner@example.com", saves=(target.id, source.id))
        create_media_alert_rule(db=db, user_id=owner_id, media_id=target.id)
        source_rule = _required(
            create_media_alert_rule(db=db, user_id=owner_id, media_id=source.id),
        )
        db.commit()
        return _Catalog(
            episode_ids=(episodes[0].id, episodes[1].id, episodes[2].id),
            target_id=target.id,
            source_id=source.id,
            target_mention_id=target_mention.id,
            source_mention_id=source_mention.id,
            owner_id=owner_id,
            source_rule_id=source_rule.id,
        )


def _required(value: _Result | None) -> _Result:
    """Return ``value``, failing the test when it is ``None``."""
    if value is None:
        raise AssertionError("expected a value, got None")
    return value


def _setup(postgres: PostgresDatabase, step: Callable[[Session], _Result]) -> _Result:
    """Run one committed setup step.

    Returns:
        The step's result.
    """
    with postgres.sessions() as db:
        result = step(db)
        db.commit()
        return result


def _merge(db: Session, *, catalog: _Catalog) -> None:
    """Merge the source into the target without committing."""
    merge_ops_media(
        db=db,
        source_media_id=catalog.source_id,
        target_media_id=catalog.target_id,
    )


def _race(
    postgres: PostgresDatabase,
    catalog: _Catalog,
    *,
    merge_first: bool,
    writer: Callable[[Session], object],
    writer_pause: str,
) -> _Race:
    """Race the merge against ``writer``, pausing whichever runs first.

    Returns:
        Both outcomes and what the second side waited on.
    """
    merge = _production(partial(_merge, catalog=catalog))
    write = _production(writer)
    if merge_first:
        run = run_paused(postgres, first=merge, second=write, pause_after=_MERGE_PAUSE)
        return _Race(
            merge=run.first,
            writer=run.second,
            waited_on=run.second_waited_on,
            expected_wait=_WRITER_WAITS_ON_MEDIA,
        )
    run = run_paused(postgres, first=write, second=merge, pause_after=writer_pause)
    return _Race(
        merge=run.second,
        writer=run.first,
        waited_on=run.second_waited_on,
        expected_wait=_MERGE_WAITS_ON_MEDIA,
    )


def _events(postgres: PostgresDatabase, user_id: int) -> list[tuple[int, int, int]]:
    """Evaluate one account's rules and commit.

    Returns:
        ``(target_id, previous_count, observed_count)`` per generated event.
    """
    with postgres.sessions() as db:
        generated = evaluate_alert_rules(db=db, user_id=user_id)
        db.commit()
        return [
            (item.rule.target_id, item.event.previous_count, item.event.observed_count)
            for item in generated
        ]


def _media_rules(postgres: PostgresDatabase) -> list[tuple[int, int, int]]:
    """Return ``(user_id, target_id, baseline_count)`` of every media rule."""
    with postgres.sessions() as db:
        return [
            (rule.user_id, rule.target_id, rule.baseline_count)
            for rule in db.query(AccountAlertRule)
            .filter(AccountAlertRule.target_type == "media")
            .order_by(AccountAlertRule.user_id, AccountAlertRule.id)
        ]


def _saves(postgres: PostgresDatabase, user_id: int) -> list[int]:
    """Return the media ids one account has saved."""
    with postgres.sessions() as db:
        return sorted(
            db.scalars(
                select(AccountSavedMedia.media_id).where(
                    AccountSavedMedia.user_id == user_id,
                ),
            ),
        )


def _assert_no_dangling_references(postgres: PostgresDatabase) -> None:
    """No rule, save or mention points at a media record that is gone."""
    with postgres.sessions() as db:
        media_ids = set(db.scalars(select(Media.id)))
        rule_targets = set(
            db.scalars(
                select(AccountAlertRule.target_id).where(
                    AccountAlertRule.target_type == "media",
                ),
            ),
        )
        saved = set(db.scalars(select(AccountSavedMedia.media_id)))
        mentioned = set(db.scalars(select(Mention.media_id)))
    assert_that(rule_targets | saved | mentioned).is_subset_of(media_ids)


# --- Alert rule creation -------------------------------------------------


def _create_rule(db: Session, *, user_id: int, media_id: int) -> int | None:
    """Create a new-mention rule without committing.

    Returns:
        The rule id, or ``None`` when the target is not eligible.
    """
    rule = create_media_alert_rule(db=db, user_id=user_id, media_id=media_id)
    return None if rule is None else int(rule.id)


@RECORDS
@ORDERS
def test_merge_and_rule_creation_on_either_record(
    postgres: PostgresDatabase,
    record: str,
    merge_first: bool,
) -> None:
    """A rule created next to a merge ends on the survivor with a full baseline.

    Created after the merge, a rule on the merged-away record is refused and
    one on the survivor counts both records' mentions as seen. Created before,
    the merge moves or reconciles it. Either way nothing is reported as new.
    """
    catalog = _seed(postgres)
    reader = _setup(
        postgres,
        partial(
            _user,
            email="reader@example.com",
            saves=(catalog.target_id, catalog.source_id),
        ),
    )

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_create_rule, user_id=reader, media_id=catalog.media_id(record)),
        writer_pause=r"^INSERT INTO account_alert_rules\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    _assert_no_dangling_references(postgres)
    refused = merge_first and record == "source"
    reader_rules = [] if refused else [(reader, catalog.target_id, 2)]
    assert_that(_media_rules(postgres)).is_equal_to(
        [(catalog.owner_id, catalog.target_id, 2), *reader_rules],
    )
    assert_that(_events(postgres, reader)).is_empty()
    assert_that(_events(postgres, catalog.owner_id)).is_empty()
    race.assert_waited_on_media()


# --- Alert rule deletion -------------------------------------------------


def _raise_source_event(db: Session, *, catalog: _Catalog) -> None:
    """Publish one more source mention and evaluate, leaving a source event."""
    db.add(Mention(episode_id=catalog.episode_ids[2], media_id=catalog.source_id))
    db.flush()
    generated = evaluate_alert_rules(db=db, user_id=catalog.owner_id)
    assert_that([item.rule.id for item in generated]).is_equal_to(
        [catalog.source_rule_id],
    )


def _delete_rule(db: Session, *, user_id: int, rule_id: int) -> bool:
    """Delete one rule without committing.

    Returns:
        Whether the rule existed.
    """
    deleted: bool = delete_alert_rule(db=db, user_id=user_id, rule_id=rule_id)
    return deleted


@ORDERS
def test_merge_and_rule_deletion(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """Deleting the source rule while a merge collapses it cannot deadlock.

    Both lock the media row first: after the merge the rule is already gone;
    before it, the merge reconciles only the surviving rule.
    """
    catalog = _seed(postgres)
    _setup(postgres, partial(_raise_source_event, catalog=catalog))

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(
            _delete_rule, user_id=catalog.owner_id, rule_id=catalog.source_rule_id
        ),
        writer_pause=r"^DELETE FROM account_alert_events\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_equal_to(not merge_first)
    _assert_no_dangling_references(postgres)
    assert_that(_media_rules(postgres)).is_equal_to(
        [(catalog.owner_id, catalog.target_id, 3)],
    )
    assert_that(_events(postgres, catalog.owner_id)).is_empty()
    race.assert_waited_on_media()


# --- Save and unsave -----------------------------------------------------


def _save(db: Session, *, user_id: int, media_id: int) -> int | None:
    """Save one record without committing.

    Returns:
        The saved media id, or ``None`` when the record does not exist.
    """
    saved = save_media(db=db, user_id=user_id, media_id=media_id)
    return None if saved is None else int(saved.media.id)


def _unsave(db: Session, *, user_id: int, media_id: int) -> bool:
    """Remove one save without committing.

    Returns:
        Whether a save was removed.
    """
    removed: bool = remove_saved_media(db=db, user_id=user_id, media_id=media_id)
    return removed


@ORDERS
def test_merge_and_save_of_the_source(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """Saving the merged-away record after the merge finds it gone."""
    catalog = _seed(postgres)
    reader = _setup(postgres, partial(_user, email="reader@example.com"))

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_save, user_id=reader, media_id=catalog.source_id),
        writer_pause=r"^INSERT INTO account_saved_media\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_equal_to(
        None if merge_first else catalog.source_id,
    )
    _assert_no_dangling_references(postgres)
    assert_that(_saves(postgres, reader)).is_equal_to(
        [] if merge_first else [catalog.target_id],
    )
    assert_that(_events(postgres, catalog.owner_id)).is_empty()
    race.assert_waited_on_media()


@ORDERS
def test_merge_and_save_of_the_target_by_a_source_saver(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """Saving the survivor while the merge moves the same account's save.

    The merge repoints the source save onto the survivor's unique key; the
    save inserts that key. Taking the media lock first, one waits for the
    other and the account ends with exactly one save.
    """
    catalog = _seed(postgres)
    reader = _setup(
        postgres,
        partial(_user, email="reader@example.com", saves=(catalog.source_id,)),
    )

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_save, user_id=reader, media_id=catalog.target_id),
        writer_pause=r"^INSERT INTO account_saved_media\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_equal_to(catalog.target_id)
    _assert_no_dangling_references(postgres)
    assert_that(_saves(postgres, reader)).is_equal_to([catalog.target_id])
    race.assert_waited_on_media()


@ORDERS
def test_merge_and_unsave_of_the_source(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """Unsaving the source save the merge is moving does not fail the merge.

    After the merge the save is on the survivor and the source no longer
    exists; before it, the merge has nothing left to move.
    """
    catalog = _seed(postgres)
    reader = _setup(
        postgres,
        partial(_user, email="reader@example.com", saves=(catalog.source_id,)),
    )

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_unsave, user_id=reader, media_id=catalog.source_id),
        writer_pause=r"^DELETE FROM account_saved_media\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_equal_to(not merge_first)
    _assert_no_dangling_references(postgres)
    assert_that(_saves(postgres, reader)).is_equal_to(
        [catalog.target_id] if merge_first else [],
    )
    race.assert_waited_on_media()


@ORDERS
def test_merge_and_unsave_of_the_target_by_an_account_saving_both(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """The merge decides which save to keep only after an unsave committed.

    Unsaving the survivor first leaves the source save, which the merge then
    moves; unsaving it after the merge removes the one save left.
    """
    catalog = _seed(postgres)

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_unsave, user_id=catalog.owner_id, media_id=catalog.target_id),
        writer_pause=r"^DELETE FROM account_saved_media\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_true()
    _assert_no_dangling_references(postgres)
    assert_that(_saves(postgres, catalog.owner_id)).is_equal_to(
        [] if merge_first else [catalog.target_id],
    )
    race.assert_waited_on_media()


# --- Mention publication -------------------------------------------------


def _publish(db: Session, *, episode_id: int, media_ids: tuple[int, ...]) -> int:
    """Publish one mention per media id, in the given order, in one flush.

    Returns:
        The number of mentions written.
    """
    db.add_all(Mention(episode_id=episode_id, media_id=media) for media in media_ids)
    db.flush()
    return len(media_ids)


def _publications() -> Iterator[tuple[str, tuple[str, ...]]]:
    yield "single-source", ("source",)
    yield "single-target", ("target",)
    # Target first, then source: the order of the issue's deadlock report.
    yield "batch", ("target", "source")


@pytest.mark.parametrize(
    "records",
    [records for _, records in _publications()],
    ids=[name for name, _ in _publications()],
)
@ORDERS
def test_merge_and_mention_publication(
    postgres: PostgresDatabase,
    records: tuple[str, ...],
    merge_first: bool,
) -> None:
    """Publication and the merge serialise on the media rows.

    Published after the merge, a mention of the merged-away record fails on
    its foreign key and nothing is written; a mention of the survivor is
    reported. Published before, every new mention is reported once.
    """
    catalog = _seed(postgres)
    media_ids = tuple(catalog.media_id(record) for record in records)

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(
            _publish, episode_id=catalog.episode_ids[2], media_ids=media_ids
        ),
        writer_pause=r"^INSERT INTO mentions\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    refused = merge_first and "source" in records
    if refused:
        assert_that(race.writer.error).is_instance_of(IntegrityError)
    else:
        assert_that(race.writer.error).is_none()
    _assert_no_dangling_references(postgres)
    published = 0 if refused else len(records)
    expected = [(catalog.target_id, 2, 2 + published)] if published else []
    assert_that(_events(postgres, catalog.owner_id)).is_equal_to(expected)
    race.assert_waited_on_media()


# --- Unpublish (mention takedown) ----------------------------------------


def _add_unseen_target_mention(db: Session, *, catalog: _Catalog) -> None:
    """Publish a target mention no rule has seen yet."""
    db.add(Mention(episode_id=catalog.episode_ids[2], media_id=catalog.target_id))
    db.flush()


def _unpublish(db: Session, *, mention_id: int) -> int:
    """Execute a mention takedown without committing.

    Returns:
        The number of mentions unpublished.
    """
    result = execute_takedown(
        db,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=mention_id,
        actions=["unpublish_mentions"],
    )
    unpublished: int = result.mentions_unpublished
    return unpublished


# (record, merge_first) -> events on the next evaluation. Counts net removals
# against additions (ADR 0015): a seen mention removed after the merge
# offsets the unseen target mention.
_UNPUBLISH_EVENTS = {
    ("source", True): "unseen",
    ("target", True): "none",
    ("source", False): "unseen-after-removal",
    ("target", False): "none",
}


@RECORDS
@ORDERS
def test_merge_and_unpublish_on_either_record(
    postgres: PostgresDatabase,
    record: str,
    merge_first: bool,
) -> None:
    """A takedown and a merge serialise on the media row.

    A takedown of a source mention that waited for the merge finds the
    mention moved, refuses with a retryable error, and succeeds on retry.
    """
    catalog = _seed(postgres)
    _setup(postgres, partial(_add_unseen_target_mention, catalog=catalog))
    mention_id = (
        catalog.source_mention_id if record == "source" else catalog.target_mention_id
    )

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_unpublish, mention_id=mention_id),
        writer_pause=r"^DELETE FROM mentions\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    retried = merge_first and record == "source"
    if retried:
        assert_that(race.writer.error).is_instance_of(RetryableLockError)
    else:
        assert_that(race.writer.error).is_none()
        assert_that(race.writer.result).is_equal_to(1)
    _assert_no_dangling_references(postgres)
    expected = {
        "unseen": [(catalog.target_id, 2, 3)],
        "unseen-after-removal": [(catalog.target_id, 1, 2)],
        "none": [],
    }[_UNPUBLISH_EVENTS[(record, merge_first)]]
    assert_that(_events(postgres, catalog.owner_id)).is_equal_to(expected)
    if retried:
        assert_that(
            _setup(postgres, partial(_unpublish, mention_id=mention_id)),
        ).is_equal_to(1)
    race.assert_waited_on_media()


# --- Account deletion ----------------------------------------------------


def _delete_account(db: Session, *, user_id: int) -> int:
    """Delete one account without committing.

    Returns:
        The number of alert rules deleted.
    """
    user = _required(db.get(AccountUser, user_id))
    rules: int = delete_account(db=db, user=user).alert_rules
    return rules


@ORDERS
def test_merge_and_account_deletion(
    postgres: PostgresDatabase,
    merge_first: bool,
) -> None:
    """Deleting an account whose rules the merge reconciles cannot deadlock."""
    catalog = _seed(postgres)
    _setup(postgres, partial(_raise_source_event, catalog=catalog))

    race = _race(
        postgres,
        catalog,
        merge_first=merge_first,
        writer=partial(_delete_account, user_id=catalog.owner_id),
        writer_pause=r"^DELETE FROM account_alert_events\b",
    )

    race.assert_clean()
    assert_that(race.merge.error).is_none()
    assert_that(race.writer.error).is_none()
    assert_that(race.writer.result).is_equal_to(1 if merge_first else 2)
    _assert_no_dangling_references(postgres)
    assert_that(_media_rules(postgres)).is_empty()
    assert_that(_saves(postgres, catalog.owner_id)).is_empty()
    with postgres.sessions() as db:
        assert_that(db.query(AccountAlertEvent).count()).is_zero()
        assert_that(db.get(AccountUser, catalog.owner_id)).is_none()
    race.assert_waited_on_media()


# --- Media split and takedown --------------------------------------------


@dataclass(frozen=True, slots=True)
class _SplitCatalog:
    """A record with one mention in each of two episodes of one podcast.

    A third episode's mention was taken down before, so the record has a
    pair suppression the split copies, which bumps the change counter.
    """

    media_id: int
    kept_episode_id: int
    moved_mention_id: int
    version: int


def _seed_split(postgres: PostgresDatabase) -> _SplitCatalog:
    """Seed the split record and its earlier mention takedown.

    Returns:
        The seeded identifiers.
    """
    with postgres.sessions() as db:
        podcast = Podcast(name="Split Show", slug="split-show")
        db.add(podcast)
        db.flush()
        episodes = [
            Episode(podcast_id=podcast.id, title=f"Split {number}")
            for number in (1, 2, 3)
        ]
        media = Media(type=MediaType.BOOK, title="Foundation")
        db.add_all([*episodes, media])
        db.flush()
        kept, moved, taken_down = (
            Mention(episode_id=episode.id, media_id=media.id) for episode in episodes
        )
        db.add_all([kept, moved, taken_down])
        db.flush()
        execute_takedown(
            db,
            subject_type=TakedownSubjectType.MENTION,
            subject_id=taken_down.id,
            actions=["unpublish_mentions"],
        )
        db.commit()
        return _SplitCatalog(
            media_id=media.id,
            kept_episode_id=episodes[0].id,
            moved_mention_id=moved.id,
            version=suppression_version(db),
        )


def _split(db: Session, *, catalog: _SplitCatalog) -> int:
    """Split the moved mention into a new record without committing.

    Returns:
        The new record's id.
    """
    result = split_ops_media(
        db=db,
        media_id=catalog.media_id,
        payload=SplitOpsMediaInputData(
            mention_ids=(catalog.moved_mention_id,),
            type=MediaType.BOOK,
            title="Foundation (split)",
        ),
    )
    created: int = _required(result).created.summary.id
    return created


def _take_down_episode(db: Session, *, episode_id: int) -> int:
    """Execute an episode mention takedown without committing.

    Returns:
        The number of mentions unpublished.
    """
    result = execute_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=episode_id,
        actions=["unpublish_mentions"],
    )
    unpublished: int = result.mentions_unpublished
    return unpublished


@pytest.mark.parametrize(
    "split_first", [True, False], ids=["split-first", "takedown-first"]
)
def test_split_and_takedown_of_the_same_podcast_cannot_deadlock(
    postgres: PostgresDatabase,
    split_first: bool,
) -> None:
    """A split takes its media row, then podcast rows, then the counter last.

    A takedown takes the same order, so the second waits on the media row
    and both finish; with the counter taken before the podcast row, the two
    waited on each other.
    """
    catalog = _seed_split(postgres)
    split = _production(partial(_split, catalog=catalog))
    takedown = _production(
        partial(_take_down_episode, episode_id=catalog.kept_episode_id),
    )
    if split_first:
        run = run_paused(
            postgres,
            first=split,
            second=takedown,
            pause_after=r"^UPDATE content_suppression_versions\b",
        )
        split_outcome, takedown_outcome = run.first, run.second
        expected_wait = _WRITER_WAITS_ON_MEDIA
    else:
        run = run_paused(
            postgres,
            first=takedown,
            second=split,
            pause_after=r"^DELETE FROM mentions\b",
        )
        takedown_outcome, split_outcome = run.first, run.second
        expected_wait = _MERGE_WAITS_ON_MEDIA

    for outcome in (split_outcome, takedown_outcome):
        _assert_not_deadlocked(outcome)
    assert_that(split_outcome.error).is_none()
    assert_that(takedown_outcome.error).is_none()
    assert_that(takedown_outcome.result).is_equal_to(1)
    _assert_no_dangling_references(postgres)
    with postgres.sessions() as db:
        moved = _required(db.get(Mention, catalog.moved_mention_id))
        assert_that(moved.media_id).is_equal_to(split_outcome.result)
        assert_that(
            db.query(Mention).filter(Mention.media_id == catalog.media_id).count(),
        ).is_zero()
        assert_that(
            db.query(ContentSuppression)
            .filter(ContentSuppression.media_id == split_outcome.result)
            .count(),
        ).is_equal_to(1)
        assert_that(suppression_version(db)).is_equal_to(catalog.version + 2)
    assert_that(run.second_waited_on).is_not_none()
    assert_that(run.second_waited_on).matches(expected_wait)
