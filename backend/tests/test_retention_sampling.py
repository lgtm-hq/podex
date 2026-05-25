"""Tests for stratified permanent transcript sampling."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import Episode, MentionCandidate, Podcast, Transcript
from podex.services.retention_sampling import (
    get_retention_sampling_report,
    recalculate_retention_sample,
)
from podex.services.transcript_retention import TranscriptRetentionPolicy


def test_recalculate_retention_sample_selects_target_with_persisted_strata(
    db_session: Session,
) -> None:
    """Verify a stratum receives its deterministic target number of exemptions."""
    now = datetime(2026, 5, 24, tzinfo=UTC)
    podcast = Podcast(name="Sampling Podcast", slug="sampling-podcast")
    db_session.add(podcast)
    db_session.flush()
    for index in range(10):
        episode = Episode(podcast_id=podcast.id, title=f"Episode {index}")
        db_session.add(episode)
        db_session.flush()
        db_session.add(
            MentionCandidate(
                episode_id=episode.id,
                media_type="book",
                raw_title=f"Book {index}",
                confidence=0.95,
            )
        )
        db_session.add(
            Transcript(
                episode_id=episode.id,
                provider="rss",
                raw_text="calibration text",
                fetched_at=now - timedelta(days=1),
            )
        )
    db_session.flush()

    report = recalculate_retention_sample(
        db=db_session,
        policy=TranscriptRetentionPolicy(
            retention_sample_rate=0.1,
            sample_version="sample-policy-v2",
        ),
        now=now,
    )
    db_session.commit()

    assert_that(report.eligible_count).is_equal_to(10)
    assert_that(report.sampled_count).is_equal_to(1)
    assert_that(report.strata[0].source).is_equal_to("sampling-podcast")
    assert_that(report.strata[0].topic).is_equal_to("book")
    selected = (
        db_session.query(Transcript)
        .filter(Transcript.retention_exempt_sample.is_(True))
        .all()
    )
    assert_that(selected).is_length(1)
    assert_that(selected[0].retention_policy_version).is_equal_to("sample-policy-v2")
    assert_that(selected[0].retention_sample_strata_json).contains_entry(
        {"confidence_band": "high"},
    )

    persisted = get_retention_sampling_report(db=db_session)
    assert_that(persisted.policy_version).is_equal_to("sample-policy-v2")
    assert_that(persisted.sample_rate).is_equal_to(0.1)
