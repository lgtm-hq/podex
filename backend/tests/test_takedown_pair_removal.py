"""A mention takedown removes its whole pair; merges and splits keep it removed.

Suppression is per ``(episode, media)`` pair, so execution deletes every
mention of a suppressed pair, merges delete survivors a carried suppression
now covers, splits copy the source record's pair suppressions to the new
record, and deleted mention ids leave every pending alert event.
"""

from dataclasses import dataclass

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from podex.models import (
    AccountUser,
    ContentSuppressedError,
    ContentSuppressionVersion,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
    TakedownRequest,
    TakedownSubjectType,
)
from podex.services.account_alerts import create_alert_rule, evaluate_alert_rules
from podex.services.account_saves import save_media
from podex.services.content_suppression import (
    bump_suppression_version,
    count_published_mentions,
    suppression_version,
)
from podex.services.ops_media_commands import (
    SplitOpsMediaInputData,
    merge_ops_media,
    split_ops_media,
)
from podex.services.ops_podcast_queries import get_ops_podcast_by_id
from tests.conftest import enable_ops
from tests.takedown_support import (
    approve_takedown,
    deliver_nothing,
    take_down_mention,
    take_down_named,
)


@dataclass(frozen=True, slots=True)
class _Catalog:
    """One podcast, two episodes and three book records."""

    podcast: int
    first: int
    second: int
    dune: int
    dupe: int
    other: int


def _catalog(db: Session) -> _Catalog:
    podcast = Podcast(name="Show", slug="show")
    db.add(podcast)
    db.flush()
    first = Episode(podcast_id=podcast.id, title="First")
    second = Episode(podcast_id=podcast.id, title="Second")
    dune = Media(type=MediaType.BOOK, title="Dune")
    dupe = Media(type=MediaType.BOOK, title="Dune (dupe)")
    other = Media(type=MediaType.BOOK, title="Dune (other)")
    db.add_all([first, second, dune, dupe, other])
    db.commit()
    return _Catalog(
        podcast=podcast.id,
        first=first.id,
        second=second.id,
        dune=dune.id,
        dupe=dupe.id,
        other=other.id,
    )


def _mention(db: Session, episode_id: int, media_id: int, context: str = "") -> int:
    mention = Mention(episode_id=episode_id, media_id=media_id, context=context)
    db.add(mention)
    db.commit()
    mention_id: int = mention.id
    return mention_id


def test_mention_takedown_removes_every_mention_of_its_pair(
    db_session: Session,
) -> None:
    """Both mentions of the pair go; the case records the extra one."""
    catalog = _catalog(db_session)
    named = _mention(db_session, catalog.first, catalog.dune, "named")
    _mention(db_session, catalog.first, catalog.dune, "same work, same episode")
    kept = _mention(db_session, catalog.second, catalog.dune, "other episode")

    result = approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=named,
        actions=["unpublish_mentions"],
    )

    assert_that([row.id for row in db_session.query(Mention)]).is_equal_to([kept])
    assert_that(result.mentions_unpublished).is_equal_to(2)
    case = db_session.query(TakedownRequest).one()
    assert_that(case.metadata_json).contains_entry({"pair_mentions_removed": 1})


def test_ops_and_public_mention_counts_agree_after_a_takedown(
    client: TestClient,
    db_session: Session,
) -> None:
    """The ops podcast count and the public count see the same mentions."""
    catalog = _catalog(db_session)
    named = _mention(db_session, catalog.first, catalog.dune)
    _mention(db_session, catalog.first, catalog.dune)
    _mention(db_session, catalog.second, catalog.dune)
    take_down_mention(db_session, named)
    enable_ops(client, api_key="test-ops-key")

    ops = client.get(
        f"/api/v2/ops/podcasts/{catalog.podcast}",
        headers={"X-Ops-Key": "test-ops-key"},
    )

    assert_that(ops.status_code).is_equal_to(200)
    assert_that(ops.json()["mention_count"]).is_equal_to(
        count_published_mentions(db_session),
    )
    assert_that(
        get_ops_podcast_by_id(db=db_session, podcast_id=catalog.podcast),
    ).is_not_none()


@pytest.mark.parametrize(
    ("source", "target"),
    [("dune", "dupe"), ("dupe", "dune"), ("dune", "other")],
    ids=["a-into-b", "b-into-a", "a-into-c"],
)
def test_merges_after_a_mention_takedown_succeed(
    db_session: Session,
    source: str,
    target: str,
) -> None:
    """A taken-down pair no longer blocks merging either record."""
    catalog = _catalog(db_session)
    named = _mention(db_session, catalog.first, catalog.dune)
    _mention(db_session, catalog.first, catalog.dune)
    _mention(db_session, catalog.first, catalog.dupe, "survivor now covered")
    take_down_mention(db_session, named)
    ids = {"dune": catalog.dune, "dupe": catalog.dupe, "other": catalog.other}

    merge_ops_media(
        db=db_session,
        source_media_id=ids[source],
        target_media_id=ids[target],
    )
    db_session.commit()

    assert_that(
        count_published_mentions(db_session, Mention.episode_id == catalog.first),
    ).is_equal_to(
        db_session.query(Mention).filter(Mention.episode_id == catalog.first).count(),
    )


def test_merge_deletes_a_survivor_its_carried_suppression_covers(
    db_session: Session,
) -> None:
    """The survivor's mention of the merged work in that episode is removed."""
    catalog = _catalog(db_session)
    named = _mention(db_session, catalog.first, catalog.dupe)
    take_down_mention(db_session, named)
    _mention(db_session, catalog.first, catalog.dune, "survivor")

    merge_ops_media(
        db=db_session,
        source_media_id=catalog.dupe,
        target_media_id=catalog.dune,
    )
    db_session.commit()

    assert_that(db_session.query(Mention).count()).is_zero()


@pytest.mark.parametrize(
    ("subject", "actions"),
    [
        ("mention", ["unpublish_mentions"]),
        ("episode", ["unpublish_mentions"]),
        ("podcast", ["unpublish_mentions"]),
    ],
)
def test_merge_then_split_cannot_republish_a_taken_down_pair(
    client: TestClient,
    db_session: Session,
    subject: str,
    actions: list[str],
) -> None:
    """Merge A into B, split B back out: the new record keeps A's suppression."""
    catalog = _catalog(db_session)
    elsewhere = Podcast(name="Elsewhere", slug="elsewhere")
    db_session.add(elsewhere)
    db_session.flush()
    outside = Episode(podcast_id=elsewhere.id, title="Outside")
    db_session.add(outside)
    db_session.commit()
    only = _mention(db_session, catalog.first, catalog.dupe, "taken down")
    moved = _mention(db_session, outside.id, catalog.dupe, "public")
    take_down_named(
        db_session,
        subject,
        {"mention": only, "episode": catalog.first, "podcast": catalog.podcast},
        actions,
    )
    merge_ops_media(
        db=db_session,
        source_media_id=catalog.dupe,
        target_media_id=catalog.dune,
    )
    db_session.commit()
    split = split_ops_media(
        db=db_session,
        media_id=catalog.dune,
        payload=SplitOpsMediaInputData(
            mention_ids=(moved,),
            type=MediaType.BOOK,
            title="Dune (split)",
        ),
    )
    db_session.commit()
    if split is None:
        raise AssertionError("split found no source record")

    db_session.add(
        Mention(episode_id=catalog.first, media_id=split.created.summary.id),
    )
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()

    listed = client.get(f"/api/v2/episodes/{catalog.first}/mentions").json()
    assert_that(listed["total"]).is_zero()


def test_digest_ignores_a_reused_mention_id(db_session: Session) -> None:
    """A deleted mention's id leaves pending events, so reuse cannot match it."""
    catalog = _catalog(db_session)
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    save_media(db=db_session, user_id=user.id, media_id=catalog.dune)
    create_alert_rule(
        db=db_session,
        user_id=user.id,
        target_type="media",
        target_id=catalog.dune,
        event_type="new_mention",
    )
    db_session.commit()
    first = _mention(db_session, catalog.first, catalog.dune)
    assert_that(evaluate_alert_rules(db=db_session, user_id=user.id)).is_length(1)
    db_session.commit()
    take_down_mention(db_session, first)
    reused = _mention(db_session, catalog.second, catalog.dune, "unrelated")
    assert_that(reused).is_equal_to(first)

    held = deliver_nothing(db_session, user)

    assert_that(held.mention_ids_json).is_empty()


def test_stats_are_current_after_a_merge_deletes_a_covered_mention(
    client: TestClient,
    db_session: Session,
) -> None:
    """The first stats read after a merge that deletes mentions is current."""
    catalog = _catalog(db_session)
    named = _mention(db_session, catalog.first, catalog.dupe)
    take_down_mention(db_session, named)
    _mention(db_session, catalog.first, catalog.dune, "survivor")
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(1)

    merge_ops_media(
        db=db_session,
        source_media_id=catalog.dupe,
        target_media_id=catalog.dune,
    )
    db_session.commit()

    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_zero()


def test_counter_row_exists_before_the_first_bump(db_session: Session) -> None:
    """The counter row comes with the table, so a bump only updates it.

    Two first bumps on PostgreSQL then queue on the row lock instead of both
    inserting the row, where one would fail on the primary key.
    """
    row = db_session.get(ContentSuppressionVersion, 1)

    assert_that(row).is_not_none()
    assert_that(suppression_version(db_session)).is_zero()
    bump_suppression_version(db_session)
    assert_that(suppression_version(db_session)).is_equal_to(1)


def test_stats_version_never_repeats_after_suppressions_are_deleted(
    client: TestClient,
    db_session: Session,
) -> None:
    """A cascade delete and a new takedown cannot bring back an old version."""
    catalog = _catalog(db_session)
    elsewhere = Podcast(name="Elsewhere", slug="elsewhere")
    db_session.add(elsewhere)
    db_session.flush()
    outside = Episode(podcast_id=elsewhere.id, title="Outside")
    db_session.add(outside)
    db_session.commit()
    first = _mention(db_session, catalog.first, catalog.dune)
    _mention(db_session, outside.id, catalog.dune)
    take_down_mention(db_session, first)
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(1)
    db_session.execute(delete(Podcast).where(Podcast.id == catalog.podcast))
    db_session.commit()
    _mention(db_session, outside.id, catalog.dupe)
    second = _mention(db_session, outside.id, catalog.other)
    take_down_mention(db_session, second)

    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(2)
