"""The suppression predicate correlates to the row it is asked about.

Podcast coverage is the case that needs an episode lookup, so each test uses
two suppressed podcasts and one podcast without a takedown, with mentions
restored around the ORM guard, and checks every form the predicate is used in:
a literal episode id, a mention's columns, and an episode's id.
"""

from dataclasses import dataclass

from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressionScope,
    Episode,
    Media,
    MediaType,
    Mention,
    Podcast,
    TakedownSubjectType,
)
from podex.models.content_suppression import suppressed_clause
from podex.services.content_suppression import (
    count_published_mentions,
    is_episode_suppressed,
    is_mention_pair_suppressed,
    published_mentions,
    suppressed_episode_ids,
)
from tests.takedown_support import approve_takedown


@dataclass(frozen=True, slots=True)
class _Catalog:
    """One open and two suppressed podcasts, one episode each, one media."""

    open_episode: int
    suppressed_episodes: tuple[int, int]
    media_id: int


def _catalog(db: Session) -> _Catalog:
    """Seed the podcasts, take two down, and restore their mentions in bulk."""
    media = Media(type=MediaType.BOOK, title="Dune")
    db.add(media)
    episodes: list[tuple[int, int]] = []
    for index in range(3):
        podcast = Podcast(name=f"Show {index}", slug=f"show-{index}")
        db.add(podcast)
        db.flush()
        episode = Episode(podcast_id=podcast.id, title=f"Episode {index}")
        db.add(episode)
        db.flush()
        db.add(
            Mention(episode_id=episode.id, media_id=media.id, context=f"open {index}"),
        )
        episodes.append((podcast.id, episode.id))
    db.commit()
    for podcast_id, episode_id in episodes[1:]:
        approve_takedown(
            db,
            subject_type=TakedownSubjectType.PODCAST,
            subject_id=podcast_id,
            actions=["unpublish_mentions", "suppress_derivatives"],
        )
        db.execute(
            insert(Mention).values(
                episode_id=episode_id,
                media_id=media.id,
                context="secret restored",
            ),
        )
    db.commit()
    return _Catalog(
        open_episode=episodes[0][1],
        suppressed_episodes=(episodes[1][1], episodes[2][1]),
        media_id=media.id,
    )


def test_literal_episode_ids_resolve_their_own_podcast(db_session: Session) -> None:
    """Direct id form: each episode answers for its own podcast only."""
    catalog = _catalog(db_session)

    for episode_id in catalog.suppressed_episodes:
        for scope in ContentSuppressionScope:
            assert_that(
                is_episode_suppressed(
                    db=db_session, episode_id=episode_id, scope=scope
                ),
            ).described_as(f"{episode_id} {scope}").is_true()
        assert_that(
            is_mention_pair_suppressed(
                db=db_session,
                episode_id=episode_id,
                media_id=catalog.media_id,
            ),
        ).is_true()
    assert_that(
        is_mention_pair_suppressed(
            db=db_session,
            episode_id=catalog.open_episode,
            media_id=catalog.media_id,
        ),
    ).is_false()


def test_episode_column_form_is_correlated(db_session: Session) -> None:
    """Episode-column form: the batch helper and a raw select agree per row."""
    catalog = _catalog(db_session)
    every_episode = [catalog.open_episode, *catalog.suppressed_episodes]

    batched = suppressed_episode_ids(
        db=db_session,
        scope=ContentSuppressionScope.DERIVATIVES,
        episode_ids=every_episode,
    )
    selected = set(
        db_session.scalars(
            select(Episode.id).where(
                suppressed_clause(
                    scope=ContentSuppressionScope.MENTIONS,
                    episode_id=Episode.id,
                ),
            ),
        ),
    )

    assert_that(batched).is_equal_to(set(catalog.suppressed_episodes))
    assert_that(selected).is_equal_to(set(catalog.suppressed_episodes))


def test_mention_column_form_is_correlated(db_session: Session) -> None:
    """Mention-column form: only the open podcast's mention is published."""
    _catalog(db_session)

    published = db_session.scalars(published_mentions()).all()

    assert_that([mention.context for mention in published]).is_equal_to(["open 0"])
    assert_that(count_published_mentions(db_session)).is_equal_to(1)


def test_public_reads_hide_every_suppressed_podcast(
    client: TestClient,
    db_session: Session,
) -> None:
    """Lists and counts drop both suppressed podcasts' restored mentions."""
    catalog = _catalog(db_session)

    for episode_id in catalog.suppressed_episodes:
        body = client.get(f"/api/v2/episodes/{episode_id}/mentions").json()
        assert_that(body["total"]).described_as(str(episode_id)).is_zero()
    media_mentions = client.get(f"/api/v2/media/{catalog.media_id}/mentions").json()
    assert_that(media_mentions["total"]).is_equal_to(1)
    assert_that([item["episode_id"] for item in media_mentions["items"]]).is_equal_to(
        [catalog.open_episode],
    )
    assert_that(client.get("/api/v2/stats").json()["mentions"]).is_equal_to(1)
