"""Unit tests for the read-side query services.

These exercise :mod:`podex.services.podcast_queries`,
:mod:`podex.services.episode_queries`, and
:mod:`podex.services.media_queries` directly against a real ``Session`` so
route handlers only need to translate service outcomes to HTTP responses.
"""

from datetime import UTC, datetime
from typing import cast

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Media, MediaType, Mention, Podcast
from podex.services import episode_queries, media_queries, podcast_queries


def test_list_podcasts_orders_by_name(db_session: Session) -> None:
    """Podcasts are returned alphabetically by name."""
    db_session.add(Podcast(name="Zeta Cast", slug="zeta"))
    db_session.add(Podcast(name="Alpha Cast", slug="alpha"))
    db_session.commit()

    result = podcast_queries.list_podcasts(db_session)

    assert_that([podcast.slug for podcast in result]).is_equal_to(["alpha", "zeta"])


def test_get_podcast_returns_none_for_missing(db_session: Session) -> None:
    """A missing podcast id resolves to ``None`` (the API maps this to 404)."""
    assert_that(podcast_queries.get_podcast(db_session, 999)).is_none()


def test_get_podcast_returns_row(db_session: Session) -> None:
    """A known podcast id returns the ORM row."""
    podcast = Podcast(name="Show", slug="show")
    db_session.add(podcast)
    db_session.commit()

    assert_that(podcast_queries.get_podcast(db_session, podcast.id).slug).is_equal_to(
        "show",
    )


def test_list_episodes_filters_and_orders(db_session: Session) -> None:
    """Episodes filter by podcast_id and sort newest-first."""
    first = Podcast(name="First", slug="first")
    second = Podcast(name="Second", slug="second")
    db_session.add_all([first, second])
    db_session.commit()
    db_session.add(
        Episode(
            podcast_id=first.id,
            title="Old",
            published_at=datetime(2020, 1, 1, tzinfo=UTC),
        ),
    )
    db_session.add(
        Episode(
            podcast_id=first.id,
            title="New",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
    )
    db_session.add(Episode(podcast_id=second.id, title="Other"))
    db_session.commit()

    filtered = episode_queries.list_episodes(db_session, podcast_id=first.id)

    assert_that([episode.title for episode in filtered]).is_equal_to(["New", "Old"])


def test_list_episode_mentions_returns_none_for_missing(db_session: Session) -> None:
    """A missing parent episode yields ``None`` so the API can 404."""
    assert_that(episode_queries.list_episode_mentions(db_session, 999)).is_none()


def test_list_episode_mentions_orders_by_timestamp(db_session: Session) -> None:
    """Mentions for an existing episode order by ``timestamp_seconds``."""
    podcast = Podcast(name="Show", slug="show")
    db_session.add(podcast)
    db_session.commit()
    episode = Episode(podcast_id=podcast.id, title="Pilot")
    dune = Media(type=MediaType.BOOK, title="Dune")
    arrival = Media(type=MediaType.MOVIE, title="Arrival")
    db_session.add_all([episode, dune, arrival])
    db_session.commit()
    db_session.add(
        Mention(episode_id=episode.id, media_id=dune.id, timestamp_seconds=120),
    )
    db_session.add(
        Mention(episode_id=episode.id, media_id=arrival.id, timestamp_seconds=30),
    )
    db_session.commit()

    mentions = episode_queries.list_episode_mentions(db_session, episode.id)

    assert_that(mentions).is_not_none()
    # ``assert_that(...).is_not_none()`` doesn't narrow the type for mypy, so
    # cast to the non-optional variant before iterating.
    narrowed = cast("list[Mention]", mentions)
    assert_that([mention.timestamp_seconds for mention in narrowed]).is_equal_to(
        [30, 120],
    )


def test_list_media_filters_by_type(db_session: Session) -> None:
    """The optional media_type filter narrows the result set."""
    db_session.add(Media(type=MediaType.BOOK, title="Dune"))
    db_session.add(Media(type=MediaType.MOVIE, title="Arrival"))
    db_session.commit()

    books = media_queries.list_media(db_session, media_type=MediaType.BOOK)

    assert_that([media.title for media in books]).is_equal_to(["Dune"])


def test_list_media_mentions_returns_none_for_missing(db_session: Session) -> None:
    """A missing parent media item yields ``None`` so the API can 404."""
    assert_that(media_queries.list_media_mentions(db_session, 999)).is_none()
