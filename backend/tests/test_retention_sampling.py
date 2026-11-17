"""Tests for stratified permanent transcript sampling."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    Episode,
    MentionCandidate,
    Podcast,
    Transcript,
    TranscriptArtifact,
)
from podex.services.retention_sampling import (
    get_retention_sampling_report,
    recalculate_retention_sample,
)
from podex.services.transcript_retention import TranscriptRetentionPolicy
from tests.json_comparison import captured_statements, json_distinct_columns


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


def test_retention_sampling_counts_artifact_backed_transcripts_once(
    db_session: Session,
) -> None:
    """Each retained transcript counts once, in SQL PostgreSQL accepts.

    A transcript with several stored artifacts is one candidate; one whose
    only artifact is purged, with no inline text, is not a candidate.
    """
    now = datetime(2026, 5, 24, tzinfo=UTC)
    podcast = Podcast(name="Artifact Podcast", slug="artifact-podcast")
    db_session.add(podcast)
    db_session.flush()
    inline, stored, purged = (
        Transcript(
            episode=Episode(podcast_id=podcast.id, title=title),
            provider="rss",
            raw_text=raw_text,
            fetched_at=now - timedelta(days=1),
            segments_json=[{"text": title}],
        )
        for title, raw_text in (
            ("Inline", "inline text"),
            ("Stored", None),
            ("Purged", None),
        )
    )
    db_session.add_all([inline, stored, purged])
    db_session.flush()
    db_session.add_all(
        [
            TranscriptArtifact(
                transcript_id=transcript.id,
                episode_id=transcript.episode_id,
                storage_key=f"transcripts/{transcript.id}/{index}.json.enc",
                storage_backend="fake",
                encryption_key_id="key",
                provider="rss",
                source_text_hash=f"{index:064x}",
                content_type="application/json",
                byte_size=12,
                stored_at=now,
                purged_at=purged_at,
            )
            for index, (transcript, purged_at) in enumerate(
                (
                    (inline, None),
                    (stored, None),
                    (stored, None),
                    (purged, now),
                ),
            )
        ],
    )
    db_session.flush()

    with captured_statements(db_session) as statements:
        recalculated = recalculate_retention_sample(
            db=db_session,
            policy=TranscriptRetentionPolicy(retention_sample_rate=0.5),
            now=now,
        )
        persisted = get_retention_sampling_report(db=db_session)

    assert_that(recalculated.eligible_count).is_equal_to(2)
    assert_that(persisted.eligible_count).is_equal_to(2)
    assert_that(statements).is_not_empty()
    for statement in statements:
        assert_that(json_distinct_columns(statement)).is_empty()
