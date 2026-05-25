"""Tests for recurring reindex and transcript digest maintenance."""

from datetime import UTC, datetime, timedelta

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    AuditLog,
    Episode,
    Media,
    MentionCandidate,
    Podcast,
    ScheduledWorkItemModel,
    ScheduledWorkStatus,
    SearchProjectionRepair,
    Transcript,
    TranscriptSourceRetentionPolicy,
)
from podex.services.recurring_maintenance import (
    REINDEX_WORK_KIND,
    TRANSCRIPT_DIGEST_WORK_KIND,
    TRANSCRIPT_RETENTION_WORK_KIND,
    evaluate_saved_transcript_retention_policies,
    generate_missing_transcript_digests,
    plan_recurring_maintenance_work,
    run_due_maintenance_work,
)


def test_plan_recurring_maintenance_work_creates_all_maintenance_items(
    db_session: Session,
) -> None:
    """Verify recurring maintenance planning persists due work items."""
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)

    work_items = plan_recurring_maintenance_work(db=db_session, now=now)
    db_session.commit()

    assert_that(work_items).is_length(3)
    metadata_values: list[dict[str, object]] = []
    for item in work_items:
        assert item.metadata_json is not None
        metadata_values.append(item.metadata_json)
    assert_that([metadata["kind"] for metadata in metadata_values]).contains(
        REINDEX_WORK_KIND,
        TRANSCRIPT_DIGEST_WORK_KIND,
        TRANSCRIPT_RETENTION_WORK_KIND,
    )
    assert_that(
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.PENDING.value)
        .count(),
    ).is_equal_to(3)


def test_run_due_maintenance_work_executes_all_maintenance_kinds(
    db_session: Session,
) -> None:
    """Verify maintenance runner completes reindex and digest work."""
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    podcast = Podcast(name="Maintenance Podcast", slug="maintenance-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Maintenance Episode")
    media = Media(type="book", title="Maintenance Book")
    db_session.add_all([episode, media])
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="A useful transcript that needs a compact digest.",
        segments_json=[],
        fetched_at=now - timedelta(days=1),
    )
    db_session.add(transcript)
    plan_recurring_maintenance_work(db=db_session, now=now)
    db_session.commit()

    results = run_due_maintenance_work(db=db_session, now=now)
    db_session.commit()

    assert_that(results).is_length(3)
    assert_that({result.kind for result in results}).is_equal_to(
        {
            REINDEX_WORK_KIND,
            TRANSCRIPT_DIGEST_WORK_KIND,
            TRANSCRIPT_RETENTION_WORK_KIND,
        },
    )
    assert_that(db_session.query(SearchProjectionRepair).count()).is_equal_to(2)
    assert_that(transcript.digest_text).is_equal_to(
        "A useful transcript that needs a compact digest.",
    )
    assert_that(transcript.digest_created_at).is_equal_to(now.replace(tzinfo=None))
    assert_that(
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.status == ScheduledWorkStatus.COMPLETED.value)
        .count(),
    ).is_equal_to(3)


def test_generate_missing_transcript_digests_skips_purged_transcripts(
    db_session: Session,
) -> None:
    """Verify digest generation ignores transcripts that already purged raw text."""
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    podcast = Podcast(name="Digest Podcast", slug="digest-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Digest Episode")
    db_session.add(episode)
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text=None,
        cleaned_text=None,
        segments_json=None,
        fetched_at=now,
        purged_at=now,
    )
    db_session.add(transcript)
    db_session.flush()

    processed_count = generate_missing_transcript_digests(db=db_session, now=now)

    assert_that(processed_count).is_zero()
    assert_that(transcript.digest_text).is_none()


def test_evaluate_saved_transcript_retention_policies_updates_and_audits(
    db_session: Session,
) -> None:
    """Verify scheduled retention uses a saved source-scoped policy."""
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    podcast = Podcast(name="Policy Podcast", slug="policy-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Old Episode")
    db_session.add(episode)
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="podscripts",
        raw_text="Retain this only until derivative safety gates allow deletion.",
        segments_json=[],
        digest_text="Compact derivative",
        digest_created_at=now - timedelta(days=1),
        fetched_at=now - timedelta(days=45),
    )
    policy = TranscriptSourceRetentionPolicy(
        podcast_id=podcast.id,
        source_key="podscripts",
        policy_version="policy-v3",
        hot_days=7,
        warm_days=30,
        min_purge_confidence=0.85,
        source_retention_opt_out=False,
    )
    candidate = MentionCandidate(
        episode_id=episode.id,
        media_type="book",
        raw_title="A Reference",
        confidence=0.96,
    )
    db_session.add_all([transcript, policy, candidate])
    db_session.flush()

    processed_count = evaluate_saved_transcript_retention_policies(
        db_session,
        now,
    )

    assert_that(processed_count).is_equal_to(1)
    assert_that(transcript.retention_policy_version).is_equal_to("policy-v3")
    assert_that(transcript.retention_tier).is_equal_to("cold")
    assert_that(transcript.purge_eligible_at).is_equal_to(now)
    audit = db_session.query(AuditLog).one()
    assert_that(audit.action).is_equal_to("evaluate_transcript_retention")
    assert_that(audit.actor_name).is_equal_to("system:retention-maintenance")
