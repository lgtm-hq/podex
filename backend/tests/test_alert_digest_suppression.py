"""Digests report only the still-published mentions each alert event covers.

A rule baselines on five published mentions of a media record; three new ones
raise an event 5 -> 8. Takedowns then hide some mentions. Hiding older
mentions must not touch the event; hiding the event's own mentions reduces
what it reports, and an event with none left is not delivered. Events are
never deleted on delivery.
"""

from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountUser,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
)
from podex.services.account_alerts import create_alert_rule, evaluate_alert_rules
from podex.services.account_digests import send_pending_digest
from podex.services.account_saves import save_media
from tests.takedown_support import take_down_mention

_OLD = 5
_NEW = 3


@dataclass(frozen=True, slots=True)
class _Alerted:
    """A user with one pending 5 -> 8 event and the mentions behind it."""

    user: AccountUser
    old_mentions: list[int]
    new_mentions: list[int]


def _mention(db: Session, *, podcast: Podcast, media: Media, index: int) -> int:
    """Publish one mention in its own episode, so it can be taken down alone."""
    episode = Episode(podcast_id=podcast.id, title=f"Episode {index}")
    db.add(episode)
    db.flush()
    mention = Mention(episode_id=episode.id, media_id=media.id)
    db.add(mention)
    db.flush()
    mention_id: int = mention.id
    return mention_id


def _alerted(db: Session) -> _Alerted:
    podcast = Podcast(name="Show", slug="show")
    media = Media(type=MediaType.BOOK, title="Dune")
    user = AccountUser(email="reader@example.com")
    db.add_all([podcast, media, user])
    db.flush()
    old = [_mention(db, podcast=podcast, media=media, index=i) for i in range(_OLD)]
    save_media(db=db, user_id=user.id, media_id=media.id)
    rule = create_alert_rule(
        db=db,
        user_id=user.id,
        target_type="media",
        target_id=media.id,
        event_type="new_mention",
    )
    if rule is None:
        raise AssertionError("alert rule was not created")
    new = [
        _mention(db, podcast=podcast, media=media, index=_OLD + i) for i in range(_NEW)
    ]
    db.commit()
    events = evaluate_alert_rules(db=db, user_id=user.id)
    assert_that(
        [(item.event.previous_count, item.event.observed_count) for item in events],
    ).is_equal_to([(_OLD, _OLD + _NEW)])
    db.commit()
    return _Alerted(user=user, old_mentions=old, new_mentions=new)


def _deliver(db: Session, alerted: _Alerted) -> tuple[list[str], Mock]:
    """Send the pending digest and return its event lines and the sender."""
    sender = Mock()
    digest = send_pending_digest(db=db, user=alerted.user, sender=sender)
    db.commit()
    if digest is None:
        return [], sender
    return [
        line for line in digest.body_text.splitlines() if line.startswith("- ")
    ], sender


@pytest.mark.parametrize(
    ("hidden_old", "hidden_new", "expected"),
    [
        (0, 0, ["- 3 new mentions of Dune"]),
        (3, 0, ["- 3 new mentions of Dune"]),
        (2, 0, ["- 3 new mentions of Dune"]),
        (0, 1, ["- 2 new mentions of Dune"]),
        (2, 2, ["- 1 new mention of Dune"]),
    ],
    ids=[
        "no-takedown",
        "older-mentions-hidden-total-equals-baseline",
        "older-mentions-hidden",
        "some-of-its-own-hidden",
        "older-and-own-hidden",
    ],
)
def test_digest_reports_the_events_own_published_mentions(
    db_session: Session,
    hidden_old: int,
    hidden_new: int,
    expected: list[str],
) -> None:
    """Only the event's own still-published mentions are counted."""
    alerted = _alerted(db_session)
    for mention_id in [
        *alerted.old_mentions[:hidden_old],
        *alerted.new_mentions[:hidden_new],
    ]:
        take_down_mention(db_session, mention_id)

    lines, _ = _deliver(db_session, alerted)

    assert_that(lines).is_equal_to(expected)


def test_event_whose_mentions_are_all_hidden_is_kept_but_not_delivered(
    db_session: Session,
) -> None:
    """With nothing of its own left, no digest goes out and the event stays."""
    alerted = _alerted(db_session)
    for mention_id in alerted.new_mentions:
        take_down_mention(db_session, mention_id)

    lines, sender = _deliver(db_session, alerted)

    assert_that(lines).is_empty()
    assert_that(sender.mock_calls).is_empty()
    events = db_session.query(AccountAlertEvent).all()
    assert_that(
        [(e.previous_count, e.observed_count, e.digest_id) for e in events]
    ).is_equal_to(
        [(_OLD, _OLD + _NEW, None)],
    )


def test_event_without_recorded_mentions_is_delivered_as_recorded(
    db_session: Session,
) -> None:
    """An event raised before mentions were recorded keeps its stored count."""
    alerted = _alerted(db_session)
    db_session.query(AccountAlertEvent).update(
        {AccountAlertEvent.mention_ids_json: None}
    )
    db_session.commit()
    take_down_mention(db_session, alerted.new_mentions[0])

    lines, _ = _deliver(db_session, alerted)

    assert_that(lines).is_equal_to(["- 3 new mentions of Dune"])
