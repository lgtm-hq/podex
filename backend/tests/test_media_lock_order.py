"""The media-reference lock order on the default (SQLite) lane (ADR 0020).

SQLite ignores row-lock clauses and has one writer, so these tests check the
helper and the order in which each writer runs its statements: the media
lock first, then podcasts, then alert rules, then events and saves, and the
suppression change counter last. The PostgreSQL races are in
``tests/postgres/test_media_lock_order_postgres.py``.
"""

import re
from dataclasses import dataclass

import pytest
from assertpy import assert_that
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from podex.models import (
    AccountSavedMedia,
    AccountUser,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
    TakedownSubjectType,
)
from podex.models.suppression_guard import lock_media_rows
from podex.services.account_alerts import (
    delete_alert_rule,
    evaluate_alert_rules,
)
from podex.services.account_privacy import delete_account
from podex.services.account_saves import remove_saved_media, save_media
from podex.services.media_locks import lock_media
from podex.services.ops_media_commands import (
    SplitOpsMediaInputData,
    merge_ops_media,
    split_ops_media,
)
from tests.conftest import create_media_alert_rule
from tests.takedown_support import execute_takedown, recorded_statements

_MEDIA_LOCK = r"^SELECT media\.id AS media_id\s+FROM media\b"
_PODCAST_LOCK = r"^SELECT podcasts\.id\s+FROM podcasts JOIN episodes\b"
_RULE_LOCK = r"^SELECT .*\bFROM account_alert_rules\b.*\bORDER BY\b"


@dataclass(frozen=True, slots=True)
class _Seeded:
    """A target and a source record, each mentioned once and watched."""

    episode_ids: tuple[int, int]
    target_id: int
    source_id: int
    user_id: int
    source_rule_id: int


def _seed(db: Session) -> _Seeded:
    """Seed two same-type records an account saved and watches.

    Returns:
        The seeded identifiers.
    """
    podcast = Podcast(name="Order Show", slug="order-show")
    db.add(podcast)
    db.flush()
    episodes = [Episode(podcast_id=podcast.id, title=f"E{n}") for n in (1, 2)]
    target = Media(type=MediaType.BOOK, title="Dune")
    source = Media(type=MediaType.BOOK, title="Dune (duplicate)")
    user = AccountUser(email="order@example.com")
    db.add_all([*episodes, target, source, user])
    db.flush()
    db.add_all(
        [
            Mention(episode_id=episodes[0].id, media_id=target.id),
            Mention(episode_id=episodes[1].id, media_id=source.id),
            AccountSavedMedia(user_id=user.id, media_id=target.id),
            AccountSavedMedia(user_id=user.id, media_id=source.id),
        ],
    )
    db.flush()
    create_media_alert_rule(db=db, user_id=user.id, media_id=target.id)
    source_rule = create_media_alert_rule(db=db, user_id=user.id, media_id=source.id)
    if source_rule is None:
        raise AssertionError("the source rule was not created")
    db.commit()
    return _Seeded(
        episode_ids=(episodes[0].id, episodes[1].id),
        target_id=target.id,
        source_id=source.id,
        user_id=user.id,
        source_rule_id=source_rule.id,
    )


def _engine(db: Session) -> Engine:
    """Return the engine the session runs on."""
    engine = db.get_bind()
    if not isinstance(engine, Engine):
        raise TypeError(type(engine))
    return engine


def _position(statements: list[str], pattern: str) -> int:
    """Return the index of the first statement matching ``pattern``."""
    compiled = re.compile(pattern, re.DOTALL)
    for index, statement in enumerate(statements):
        if compiled.search(statement):
            return index
    raise AssertionError(f"no statement matched {pattern!r}: {statements}")


def _assert_in_order(statements: list[str], *patterns: str) -> None:
    """Each pattern's first statement comes after the previous pattern's."""
    positions = [_position(statements, pattern) for pattern in patterns]
    assert_that(positions).described_as(f"{patterns}").is_equal_to(sorted(positions))


def test_lock_media_rereads_the_records_that_still_exist(db_session: Session) -> None:
    """Missing ids are left out; loaded state is replaced by the stored row."""
    seeded = _seed(db_session)
    target = db_session.get(Media, seeded.target_id)
    if target is None:
        raise AssertionError("the target was not seeded")
    target.title = "unflushed edit"

    with recorded_statements(_engine(db_session)) as statements:
        locked = lock_media(
            db=db_session,
            media_ids=[seeded.source_id, 999_999, seeded.target_id],
        )

    assert_that(sorted(locked)).is_equal_to(
        sorted([seeded.target_id, seeded.source_id]),
    )
    assert_that(locked[seeded.target_id].title).is_equal_to("Dune")
    assert_that(statements).is_length(2)
    assert_that(statements[0]).matches(_MEDIA_LOCK)
    assert_that(statements[0]).matches(r"ORDER BY media\.id")


def test_an_exclusive_lock_covers_a_later_key_share_lock(db_session: Session) -> None:
    """A record locked FOR UPDATE is not locked again FOR KEY SHARE."""
    seeded = _seed(db_session)
    ids = [seeded.target_id, seeded.source_id]

    with recorded_statements(_engine(db_session)) as statements:
        lock_media_rows(session=db_session, media_ids=ids, exclusive=True)
        lock_media_rows(session=db_session, media_ids=ids)
        lock_media_rows(session=db_session, media_ids=ids, exclusive=True)

    assert_that(statements).is_length(1)


def test_a_key_share_lock_does_not_cover_a_later_exclusive_lock(
    db_session: Session,
) -> None:
    """A record locked FOR KEY SHARE is locked again FOR UPDATE when asked."""
    seeded = _seed(db_session)

    with recorded_statements(_engine(db_session)) as statements:
        lock_media_rows(session=db_session, media_ids=[seeded.target_id])
        lock_media_rows(
            session=db_session,
            media_ids=[seeded.target_id],
            exclusive=True,
        )

    assert_that(statements).is_length(2)


def test_save_and_unsave_lock_the_record_first(db_session: Session) -> None:
    """The media lock precedes the save's insert and the unsave's delete."""
    seeded = _seed(db_session)
    reader = AccountUser(email="reader@example.com")
    db_session.add(reader)
    db_session.commit()

    with recorded_statements(_engine(db_session)) as statements:
        save_media(db=db_session, user_id=reader.id, media_id=seeded.target_id)
        db_session.commit()
    _assert_in_order(statements, _MEDIA_LOCK, r"^INSERT INTO account_saved_media\b")

    with recorded_statements(_engine(db_session)) as statements:
        assert_that(
            remove_saved_media(
                db=db_session,
                user_id=reader.id,
                media_id=seeded.target_id,
            ),
        ).is_true()
    _assert_in_order(statements, _MEDIA_LOCK, r"^DELETE FROM account_saved_media\b")


def test_save_of_a_missing_record_saves_nothing(db_session: Session) -> None:
    """Saving a record that does not exist answers ``None``, as before."""
    seeded = _seed(db_session)

    assert_that(
        save_media(db=db_session, user_id=seeded.user_id, media_id=999_999),
    ).is_none()


def test_rule_creation_locks_the_record_before_reading_eligibility(
    db_session: Session,
) -> None:
    """The media lock precedes the save lookup and the rule insert."""
    seeded = _seed(db_session)
    reader = AccountUser(email="reader@example.com")
    db_session.add(reader)
    db_session.flush()
    db_session.add(AccountSavedMedia(user_id=reader.id, media_id=seeded.target_id))
    db_session.commit()

    with recorded_statements(_engine(db_session)) as statements:
        rule = create_media_alert_rule(
            db=db_session,
            user_id=reader.id,
            media_id=seeded.target_id,
        )

    assert_that(rule).is_not_none()
    _assert_in_order(
        statements,
        _MEDIA_LOCK,
        r"\bFROM account_saved_media\b",
        r"^INSERT INTO account_alert_rules\b",
    )


def test_rule_creation_on_a_missing_record_creates_nothing(
    db_session: Session,
) -> None:
    """A rule on a record that does not exist is refused, as before."""
    seeded = _seed(db_session)

    assert_that(
        create_media_alert_rule(
            db=db_session,
            user_id=seeded.user_id,
            media_id=999_999,
        ),
    ).is_none()


def test_rule_deletion_locks_media_then_rule_then_events(db_session: Session) -> None:
    """Media lock, then the rule re-read, then events, then the rule."""
    seeded = _seed(db_session)

    with recorded_statements(_engine(db_session)) as statements:
        assert_that(
            delete_alert_rule(
                db=db_session,
                user_id=seeded.user_id,
                rule_id=seeded.source_rule_id,
            ),
        ).is_true()

    _assert_in_order(
        statements,
        _MEDIA_LOCK,
        r"^DELETE FROM account_alert_events\b",
        r"^DELETE FROM account_alert_rules\b",
    )
    locked = _position(statements, _MEDIA_LOCK)
    events = _position(statements, r"^DELETE FROM account_alert_events\b")
    assert_that(
        [
            statement
            for statement in statements[locked:events]
            if re.match(r"^SELECT .*\bFROM account_alert_rules\b", statement, re.DOTALL)
        ],
    ).described_as("rule re-read under the media lock").is_length(1)


def test_deleting_a_rule_a_merge_collapsed_reports_it_missing(
    db_session: Session,
) -> None:
    """After a merge collapsed the source rule, deleting it answers ``False``."""
    seeded = _seed(db_session)
    merge_ops_media(
        db=db_session,
        source_media_id=seeded.source_id,
        target_media_id=seeded.target_id,
    )
    db_session.commit()

    assert_that(
        delete_alert_rule(
            db=db_session,
            user_id=seeded.user_id,
            rule_id=seeded.source_rule_id,
        ),
    ).is_false()


def test_account_deletion_locks_media_then_rules_then_events(
    db_session: Session,
) -> None:
    """The account's media, then its rules in id order, then their events."""
    seeded = _seed(db_session)
    user = db_session.get(AccountUser, seeded.user_id)
    if user is None:
        raise AssertionError("the account was not seeded")

    with recorded_statements(_engine(db_session)) as statements:
        result = delete_account(db=db_session, user=user)

    assert_that(result.alert_rules).is_equal_to(2)
    assert_that(result.saves).is_equal_to(2)
    _assert_in_order(
        statements,
        _MEDIA_LOCK,
        _RULE_LOCK,
        r"^DELETE FROM account_alert_events\b",
        r"^DELETE FROM account_alert_rules\b",
        r"^DELETE FROM account_saved_media\b",
    )


def test_merge_locks_media_then_podcasts_then_rules(db_session: Session) -> None:
    """The merge takes its podcast locks before alert rules, the counter last."""
    seeded = _seed(db_session)

    with recorded_statements(_engine(db_session)) as statements:
        merge_ops_media(
            db=db_session,
            source_media_id=seeded.source_id,
            target_media_id=seeded.target_id,
        )

    _assert_in_order(statements, _MEDIA_LOCK, _PODCAST_LOCK, _RULE_LOCK)
    assert_that(
        [item for item in statements if re.match(r"^(UPDATE|INSERT|DELETE)", item)][-1],
    ).matches(r"^UPDATE content_suppression_versions\b")
    assert_that(evaluate_alert_rules(db=db_session, user_id=seeded.user_id)).is_empty()


def test_split_takes_the_counter_after_moving_mentions(db_session: Session) -> None:
    """The record lock first; the counter after the flush that moves mentions."""
    seeded = _seed(db_session)
    taken_down = Mention(episode_id=seeded.episode_ids[0], media_id=seeded.source_id)
    db_session.add(taken_down)
    db_session.flush()
    execute_takedown(
        db_session,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=taken_down.id,
        actions=["unpublish_mentions"],
    )
    db_session.commit()
    moved = db_session.query(Mention).filter(Mention.media_id == seeded.source_id).one()

    with recorded_statements(_engine(db_session)) as statements:
        result = split_ops_media(
            db=db_session,
            media_id=seeded.source_id,
            payload=SplitOpsMediaInputData(
                mention_ids=(moved.id,),
                type=MediaType.BOOK,
                title="Dune (split)",
            ),
        )

    assert_that(result).is_not_none()
    _assert_in_order(
        statements,
        _MEDIA_LOCK,
        _PODCAST_LOCK,
        r"^UPDATE mentions\b",
        r"^INSERT INTO content_suppressions\b",
        r"^UPDATE content_suppression_versions\b",
    )


@pytest.mark.parametrize("title", ["", "Dune (split)"])
def test_split_of_a_missing_record_answers_none(
    db_session: Session,
    title: str,
) -> None:
    """A missing record is reported before the payload is validated."""
    assert_that(
        split_ops_media(
            db=db_session,
            media_id=999_999,
            payload=SplitOpsMediaInputData(
                mention_ids=(1,),
                type=MediaType.BOOK,
                title=title,
            ),
        ),
    ).is_none()
