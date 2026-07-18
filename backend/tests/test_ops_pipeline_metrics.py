"""Tests for ops pipeline queries, metrics, and retention commands."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountDigest,
    AccountUser,
    IngestionRun,
    IngestionRunStatus,
    MentionCandidate,
    ReviewItem,
    ReviewItemStatus,
    Transcript,
)
from podex.services.ops_metrics import get_operational_metrics
from podex.services.ops_retention_commands import (
    apply_ops_transcript_retention,
    list_ops_transcript_retention,
    preview_ops_transcript_retention,
)
from podex.services.pipeline_queries import (
    get_ingestion_run_by_id,
    list_recent_ingestion_runs,
)
from podex.services.transcript_retention import TranscriptRetentionPolicy
from tests.conftest import seed_catalog_graph

_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_ingestion_run_queries_compute_durations(db_session: Session) -> None:
    """Run summaries include whole-second durations and recency ordering."""
    done = IngestionRun(
        status=IngestionRunStatus.COMPLETED,
        started_at=_NOW - timedelta(minutes=5),
        completed_at=_NOW,
    )
    running = IngestionRun(status=IngestionRunStatus.RUNNING, started_at=_NOW)
    db_session.add_all([done, running])
    db_session.commit()

    loaded = get_ingestion_run_by_id(db=db_session, ingestion_run_id=done.id)
    assert_that(loaded).is_not_none()
    if loaded is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(loaded.duration_seconds).is_equal_to(300)
    assert_that(
        get_ingestion_run_by_id(db=db_session, ingestion_run_id=999_999),
    ).is_none()

    recent = list_recent_ingestion_runs(db=db_session, limit=10)
    assert_that(recent).is_length(2)
    assert_that(recent[0].id).is_equal_to(running.id)
    assert_that(recent[0].duration_seconds).is_none()


def test_operational_metrics_summarize_review_and_alerts(
    db_session: Session,
) -> None:
    """Dashboard metrics compute review throughput and alert delivery."""
    graph = seed_catalog_graph(db_session)
    pending_candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
        confidence=0.4,
        extraction_source="llm",
    )
    decided_candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune II",
        confidence=0.4,
        extraction_source="llm",
    )
    db_session.add_all([pending_candidate, decided_candidate])
    db_session.flush()
    db_session.add(ReviewItem(mention_candidate_id=pending_candidate.id))
    db_session.add(
        ReviewItem(
            mention_candidate_id=decided_candidate.id,
            status=ReviewItemStatus.APPROVED.value,
            created_at=_NOW - timedelta(hours=2),
            decided_at=_NOW - timedelta(hours=1),
        ),
    )
    user = AccountUser(email="reader@example.com")
    db_session.add(user)
    db_session.flush()
    rule = AccountAlertRule(
        user_id=user.id,
        target_type="podcast",
        target_id=graph.podcast_id,
        event_type="new_episode",
    )
    db_session.add(rule)
    db_session.flush()
    digest = AccountDigest(
        user_id=user.id,
        subject="Podex digest: 1 new update",
        body_text="- 1 new episode",
        event_count=1,
        created_at=_NOW - timedelta(hours=1),
        delivered_at=_NOW - timedelta(hours=1),
    )
    db_session.add(digest)
    db_session.flush()
    db_session.add_all(
        [
            AccountAlertEvent(
                rule_id=rule.id,
                previous_count=1,
                observed_count=2,
                digest_id=digest.id,
                created_at=_NOW - timedelta(hours=2),
            ),
            AccountAlertEvent(
                rule_id=rule.id,
                previous_count=2,
                observed_count=3,
                created_at=_NOW - timedelta(minutes=30),
            ),
        ],
    )
    db_session.commit()

    metrics = get_operational_metrics(db=db_session, now=_NOW)

    assert_that(metrics.review.pending_items).is_equal_to(1)
    assert_that(metrics.review.decisions_last_24h).is_equal_to(1)
    assert_that(metrics.review.median_decision_minutes_last_24h).is_equal_to(60.0)
    assert_that(metrics.alerts.generated_events_last_24h).is_equal_to(2)
    assert_that(metrics.alerts.delivered_digests_last_24h).is_equal_to(1)
    assert_that(metrics.alerts.delivered_events_last_24h).is_equal_to(1)
    assert_that(metrics.alerts.pending_events).is_equal_to(1)


def test_ops_retention_preview_and_apply(db_session: Session) -> None:
    """Retention preview reports the purge gate; apply persists evaluation."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="raw text",
        fetched_at=_NOW - timedelta(days=400),
    )
    db_session.add(transcript)
    db_session.commit()

    listed = list_ops_transcript_retention(db=db_session)
    assert_that(listed).is_length(1)
    assert_that(listed[0].has_raw_payload).is_true()
    assert_that(listed[0].has_stored_artifact).is_false()

    policy = TranscriptRetentionPolicy()
    preview = preview_ops_transcript_retention(
        db=db_session,
        transcript_id=transcript.id,
        policy=policy,
        now=_NOW,
    )
    assert_that(preview).is_not_none()
    if preview is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(preview.derivative_coverage_ready).is_false()
    assert_that(preview.missing_query_classes).is_not_empty()
    assert_that(
        preview_ops_transcript_retention(
            db=db_session,
            transcript_id=999_999,
            policy=policy,
        ),
    ).is_none()

    applied = apply_ops_transcript_retention(
        db=db_session,
        transcript_id=transcript.id,
        policy=policy,
        now=_NOW,
    )
    db_session.commit()
    assert_that(applied).is_not_none()
    if applied is None:  # pragma: no cover - narrowed above
        raise AssertionError
    assert_that(applied.transcript.tier).is_not_equal_to("hot")
