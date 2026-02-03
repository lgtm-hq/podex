"""Tests for transcript model."""

from datetime import datetime

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast, Transcript


def test_create_transcript(db_session: Session) -> None:
    podcast = Podcast(name="Test Podcast", slug="test")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(podcast_id=podcast.id, title="Episode 1")
    db_session.add(episode)
    db_session.flush()

    fetched_at = datetime(2026, 2, 3)
    transcript = Transcript(
        episode_id=episode.id,
        provider="youtube",
        raw_text="Hello world",
        segments_json=[{"start": 0, "duration": 5, "text": "Hello"}],
        fetched_at=fetched_at,
    )
    db_session.add(transcript)
    db_session.commit()

    stored = db_session.query(Transcript).first()
    assert_that(stored).is_not_none()
    assert_that(stored.episode_id).is_equal_to(episode.id)
    assert_that(stored.provider).is_equal_to("youtube")
    assert_that(stored.raw_text).is_equal_to("Hello world")
    assert_that(stored.segments_json[0]["text"]).is_equal_to("Hello")
    assert_that(stored.fetched_at).is_equal_to(fetched_at)
    assert_that(stored.episode.id).is_equal_to(episode.id)
