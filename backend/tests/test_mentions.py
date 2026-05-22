"""Tests for mention timestamp handling."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Media, Mention, Podcast


def test_youtube_timestamp_url_includes_zero(db_session: Session) -> None:
    podcast = Podcast(name="Test Podcast", slug="test")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Episode 1", youtube_id="abc123")
    db_session.add(episode)
    db_session.flush()

    media = Media(type="book", title="Test Book")
    db_session.add(media)
    db_session.flush()

    mention = Mention(
        episode_id=episode.id,
        media_id=media.id,
        timestamp_seconds=0,
    )
    db_session.add(mention)
    db_session.commit()

    stored = db_session.query(Mention).first()
    assert stored is not None
    assert_that(stored.youtube_timestamp_url).is_equal_to(
        "https://youtube.com/watch?v=abc123&t=0"
    )
