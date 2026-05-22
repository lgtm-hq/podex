"""Tests for persisted transcript retention commands."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, Podcast, Transcript
from podex.services.transcript_retention import (
    TranscriptLifecycleTier,
    TranscriptRetentionPolicy,
)
from podex.services.transcript_retention_commands import (
    evaluate_transcript_retention,
    purge_transcript_if_eligible,
)


def _create_transcript(db_session: Session, fetched_at: datetime) -> Transcript:
    """Create a transcript fixture."""
    podcast = Podcast(name="Retention Podcast", slug="retention-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Retention Episode")
    db_session.add(episode)
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="raw transcript",
        cleaned_text="clean transcript",
        segments_json=[{"start": 1, "text": "hello"}],
        fetched_at=fetched_at,
        digest_text="digest",
        digest_created_at=fetched_at,
    )
    db_session.add(transcript)
    db_session.flush()
    return transcript


def test_evaluate_transcript_retention_persists_policy_decision(
    db_session: Session,
) -> None:
    """Verify retention evaluation updates transcript lifecycle fields."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )

    result = evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )
    db_session.commit()

    assert_that(result.decision.tier).is_equal_to(TranscriptLifecycleTier.COLD)
    assert_that(transcript.retention_tier).is_equal_to("cold")
    assert_that(transcript.retention_policy_version).is_equal_to("retention-sample-v1")
    assert_that(transcript.purge_eligible_at).is_equal_to(now.replace(tzinfo=None))
    assert_that(transcript.retention_blockers_json).is_empty()


def test_purge_transcript_if_eligible_removes_raw_fields(
    db_session: Session,
) -> None:
    """Verify eligible transcripts can purge raw transcript storage."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(
        db_session=db_session,
        fetched_at=now - timedelta(days=45),
    )
    evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=0.95,
        policy=TranscriptRetentionPolicy(
            hot_retention_period=timedelta(days=7),
            warm_retention_period=timedelta(days=30),
            retention_sample_rate=0,
        ),
        now=now,
    )

    purged = purge_transcript_if_eligible(
        db=db_session,
        transcript=transcript,
        now=now,
    )
    db_session.commit()

    assert_that(purged).is_true()
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.cleaned_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(transcript.retention_tier).is_equal_to("purged")


def test_source_opt_out_allows_immediate_retention_suppression(
    db_session: Session,
) -> None:
    """Verify source opt-out bypasses derivative prerequisites."""
    now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    transcript = _create_transcript(db_session=db_session, fetched_at=now)
    transcript.digest_text = None
    transcript.digest_created_at = None

    result = evaluate_transcript_retention(
        db=db_session,
        transcript=transcript,
        extraction_confidence=None,
        source_retention_opt_out=True,
        now=now,
    )
    db_session.commit()

    assert_that(result.decision.purge_eligible).is_true()
    assert_that(transcript.source_retention_opt_out).is_true()
    assert_that(transcript.purge_eligible_at).is_equal_to(now.replace(tzinfo=None))
