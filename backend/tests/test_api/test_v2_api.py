"""Tests for version 2 API endpoints."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.v2 import admin as v2_admin_api
from podex.api.v2 import ops as v2_ops_api
from podex.api.v2 import public as v2_public_api
from podex.config import Settings
from podex.models import (
    AccountAlertEvent,
    AccountAlertRule,
    AccountDigest,
    AccountUser,
    AuditLog,
    DerivativeSummaryKind,
    EditorialCollection,
    EditorialCollectionItem,
    Episode,
    EpisodeSummary,
    IngestionRun,
    JobStatus,
    JobType,
    Media,
    MediaRelationType,
    MediaType,
    Mention,
    MentionCandidate,
    MentionCandidateProvenance,
    MentionCandidateProvenanceEventType,
    MentionCandidateState,
    Podcast,
    ReviewItem,
    ReviewItemStatus,
    ReviewPriority,
    SearchAnalyticsEvent,
    SearchProjectionRepair,
    SearchProjectionRepairReason,
    SearchProjectionRepairResourceType,
    SearchProjectionRepairStatus,
    SemanticChunk,
    TakedownRequest,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
    TranscriptionJob,
    TranscriptSourceRetentionPolicy,
)
from podex.services.derivative_summaries import (
    stable_source_text_hash,
    upsert_episode_summary,
    upsert_media_summary,
)
from podex.services.graph_relations import (
    GraphTripleInputData,
    upsert_graph_triple,
    upsert_media_relation,
)
from podex.services.transcript_artifacts import StoredTranscriptArtifactData
from podex.services.transcript_source import TranscriptAcquisitionResult
from podex.services.whisper_transcriber import TranscriptResult


@pytest.fixture
def sample_v2_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for v2 podcast and ops tests."""
    podcast1 = Podcast(
        name="Test Podcast",
        slug="test-podcast",
        description="A test podcast",
        status="active",
        discovery_source="rss",
        rss_url="https://example.com/feed.xml",
        youtube_channel_id="channel-1",
    )
    podcast2 = Podcast(
        name="Another Podcast",
        slug="another-podcast",
        description="Another test podcast",
        status="watchlist",
        discovery_source="spotify",
        spotify_id="spotify-1",
        podscripts_slug="another-podcast",
    )
    podcast3 = Podcast(
        name="Paused Podcast",
        slug="paused-podcast",
        description="Paused test podcast",
        status="paused",
    )
    db_session.add_all([podcast1, podcast2, podcast3])
    db_session.flush()

    episode1 = Episode(
        podcast_id=podcast1.id,
        title="Episode 1",
        episode_number=1,
        youtube_id="abc123",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        transcript_status="completed",
        extraction_status="completed",
    )
    episode2 = Episode(
        podcast_id=podcast1.id,
        title="Episode 2",
        episode_number=2,
        youtube_id="def456",
        published_at=datetime(2024, 1, 15, tzinfo=UTC),
        transcript_status="pending",
        extraction_status="pending",
    )
    episode3 = Episode(
        podcast_id=podcast2.id,
        title="Another Episode 1",
        episode_number=1,
        youtube_id="ghi789",
        published_at=datetime(2024, 2, 1, tzinfo=UTC),
        transcript_status="completed",
        extraction_status="pending",
    )
    db_session.add_all([episode1, episode2, episode3])
    db_session.flush()

    media = Media(type="book", title="Test Book", author="Test Author")
    db_session.add(media)
    db_session.flush()

    mention = Mention(
        episode_id=episode1.id,
        media_id=media.id,
        timestamp_seconds=60,
        context="Mentioned Test Book",
        confidence=0.9,
    )
    db_session.add(mention)

    pending_job = TranscriptionJob(
        episode_id=episode2.id,
        job_type="transcribe",
        status=JobStatus.PENDING,
        created_at=datetime(2024, 2, 1, 10, 0, tzinfo=UTC),
    )
    failed_job = TranscriptionJob(
        episode_id=episode3.id,
        job_type="extract",
        status=JobStatus.FAILED,
        backend="whisper",
        model="large-v3",
        error_message="Extraction failed",
        created_at=datetime(2024, 2, 2, 10, 0, tzinfo=UTC),
        started_at=datetime(2024, 2, 2, 10, 1, tzinfo=UTC),
        completed_at=datetime(2024, 2, 2, 10, 3, tzinfo=UTC),
    )
    db_session.add_all([pending_job, failed_job])

    run_in_progress = IngestionRun(
        status="in_progress",
        created_at=datetime(2024, 3, 1, 8, 55, tzinfo=UTC),
        started_at=datetime(2024, 3, 1, 9, 0, tzinfo=UTC),
    )
    run_failed = IngestionRun(
        status="failed",
        error_summary="Transcript provider failed",
        created_at=datetime(2024, 3, 2, 8, 55, tzinfo=UTC),
        started_at=datetime(2024, 3, 2, 9, 0, tzinfo=UTC),
        completed_at=datetime(2024, 3, 2, 9, 4, tzinfo=UTC),
    )
    run_completed = IngestionRun(
        status="completed",
        created_at=datetime(2024, 3, 3, 8, 55, tzinfo=UTC),
        started_at=datetime(2024, 3, 3, 9, 0, tzinfo=UTC),
        completed_at=datetime(2024, 3, 3, 9, 10, tzinfo=UTC),
    )
    db_session.add_all([run_in_progress, run_failed, run_completed])

    db_session.commit()

    return {
        "podcasts": [podcast1, podcast2, podcast3],
        "episodes": [episode1, episode2, episode3],
        "jobs": [pending_job, failed_job],
        "runs": [run_in_progress, run_failed, run_completed],
    }


@pytest.fixture
def sample_v2_media_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for v2 media tests."""
    podcast = Podcast(name="Media Podcast", slug="media-podcast", status="active")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Media Episode 1",
        episode_number=1,
        youtube_id="media123",
        transcript_status="completed",
        extraction_status="completed",
    )
    db_session.add(episode)
    db_session.flush()

    media1 = Media(
        type="book",
        title="The Great Gatsby",
        author="F. Scott Fitzgerald",
        year=1925,
        google_books_id="books-1",
        metadata_json={"isbn_13": "9780743273565", "genres": ["Classic"]},
    )
    media2 = Media(
        type="movie",
        title="Inception",
        author="Christopher Nolan",
        year=2010,
        imdb_id="tt1375666",
    )
    media3 = Media(
        type="book",
        title="100% Pure_Test",
        author="Test Author",
    )
    db_session.add_all([media1, media2, media3])
    db_session.flush()

    mention1 = Mention(
        episode_id=episode.id,
        media_id=media1.id,
        timestamp_seconds=60,
        context="They talked about The Great Gatsby",
        confidence=0.9,
    )
    mention2 = Mention(
        episode_id=episode.id,
        media_id=media1.id,
        timestamp_seconds=120,
        context="More about The Great Gatsby",
        confidence=0.85,
    )
    mention3 = Mention(
        episode_id=episode.id,
        media_id=media2.id,
        timestamp_seconds=180,
        context="Talking about Inception",
        confidence=0.95,
    )
    db_session.add_all([mention1, mention2, mention3])
    db_session.commit()

    return {
        "podcast": podcast,
        "episode": episode,
        "media": [media1, media2, media3],
        "mentions": [mention1, mention2, mention3],
    }


@pytest.fixture
def sample_v2_trends_data(db_session: Session) -> dict[str, Any]:
    """Create sample data for v2 trends tests."""
    podcast = Podcast(name="Trend Podcast", slug="trend-podcast", status="active")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Trend Episode 1",
        episode_number=1,
        youtube_id="trend123",
        published_at=datetime(2024, 4, 1, tzinfo=UTC),
        transcript_status="completed",
        extraction_status="completed",
    )
    db_session.add(episode)
    db_session.flush()

    book1 = Media(type=MediaType.BOOK.value, title="Book One", author="Author A")
    book2 = Media(type=MediaType.BOOK.value, title="Book Two", author="Author B")
    movie = Media(type=MediaType.MOVIE.value, title="Movie One", author="Director A")
    documentary = Media(type=MediaType.DOCUMENTARY.value, title="Doc One")
    db_session.add_all([book1, book2, movie, documentary])
    db_session.flush()

    mentions = [
        Mention(
            episode_id=episode.id,
            media_id=book1.id,
            timestamp_seconds=60,
            confidence=0.9,
        ),
        Mention(
            episode_id=episode.id,
            media_id=book1.id,
            timestamp_seconds=120,
            confidence=0.9,
        ),
        Mention(
            episode_id=episode.id,
            media_id=book1.id,
            timestamp_seconds=180,
            confidence=0.9,
        ),
        Mention(
            episode_id=episode.id,
            media_id=movie.id,
            timestamp_seconds=240,
            confidence=0.9,
        ),
    ]
    db_session.add_all(mentions)
    db_session.commit()

    return {
        "podcast": podcast,
        "episode": episode,
        "media": [book1, book2, movie, documentary],
        "mentions": mentions,
    }


@pytest.fixture
def sample_review_queue_data(db_session: Session) -> dict[str, Any]:
    """Create sample review queue data for v2 ops review tests."""
    podcast = Podcast(name="Review Podcast", slug="review-podcast", status="active")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Review Episode",
        transcript_status="completed",
        extraction_status="completed",
    )
    db_session.add(episode)
    db_session.flush()

    source_job = TranscriptionJob(
        episode_id=episode.id,
        job_type=JobType.EXTRACT.value,
        status=JobStatus.COMPLETED.value,
        backend="anthropic",
        model="claude-sonnet-4-20250514",
    )
    failed_job = TranscriptionJob(
        episode_id=episode.id,
        job_type=JobType.EXTRACT.value,
        status=JobStatus.FAILED.value,
        backend="anthropic",
        model="claude-sonnet-4-20250514",
        error_message="provider rate limit",
    )
    db_session.add_all([source_job, failed_job])
    db_session.flush()

    candidate = MentionCandidate(
        episode_id=episode.id,
        source_job_id=source_job.id,
        media_type=MediaType.BOOK.value,
        raw_title="The Reviewable Book",
        normalized_title="The Reviewable Book",
        suggested_author="Queue Author",
        timestamp_seconds=95,
        context="They recommended The Reviewable Book",
        confidence=0.73,
        extraction_source="llm_extract",
    )
    db_session.add(candidate)
    db_session.flush()

    db_session.add(
        MentionCandidateProvenance(
            mention_candidate_id=candidate.id,
            source_job_id=source_job.id,
            event_type=MentionCandidateProvenanceEventType.CREATED.value,
            change_summary="Created from extraction result",
            raw_title=candidate.raw_title,
            normalized_title=candidate.normalized_title,
            suggested_author=candidate.suggested_author,
            timestamp_seconds=candidate.timestamp_seconds,
            context=candidate.context,
            confidence=candidate.confidence,
            extraction_source=candidate.extraction_source,
            metadata_json=None,
        )
    )

    review_item = ReviewItem(
        mention_candidate_id=candidate.id,
        priority=ReviewPriority.HIGH.value,
    )
    db_session.add(review_item)
    db_session.commit()

    return {
        "podcast": podcast,
        "episode": episode,
        "candidate": candidate,
        "review_item": review_item,
        "source_job": source_job,
    }


def test_list_public_podcasts_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 public podcast listing uses opaque identifiers and stats."""
    response = client.get("/api/v2/podcasts")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data).is_length(3)
    podcast = next(item for item in data if item["slug"] == "test-podcast")
    assert_that(podcast["id"]).is_equal_to("pod_1")
    assert_that(podcast["episode_count"]).is_equal_to(2)
    assert_that(podcast["mention_count"]).is_equal_to(1)


def test_get_public_podcast_detail_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 podcast detail returns the public contract."""
    response = client.get("/api/v2/podcasts/test-podcast")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("pod_1")
    assert_that(data["slug"]).is_equal_to("test-podcast")
    assert_that(data["mention_count"]).is_equal_to(1)


def test_get_public_podcast_episodes_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 episode listing returns opaque identifiers and pagination."""
    response = client.get("/api/v2/podcasts/test-podcast/episodes?page=1&per_page=1")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["items"]).is_length(1)
    assert_that(data["total"]).is_equal_to(2)
    assert_that(data["items"][0]["id"]).is_equal_to("ep_2")
    assert_that(data["items"][0]["podcast_id"]).is_equal_to("pod_1")


def test_get_ops_dashboard_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify the v2 ops dashboard aggregates catalog and pipeline metrics."""
    response = client.get("/api/v2/ops/dashboard")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["catalog"]["total_podcasts"]).is_equal_to(3)
    assert_that(data["catalog"]["active_podcasts"]).is_equal_to(1)
    assert_that(data["catalog"]["watchlist_podcasts"]).is_equal_to(1)
    assert_that(data["catalog"]["paused_podcasts"]).is_equal_to(1)
    assert_that(data["sources"]["with_rss"]).is_equal_to(1)
    assert_that(data["sources"]["with_spotify"]).is_equal_to(1)
    assert_that(data["sources"]["with_podscripts"]).is_equal_to(1)
    assert_that(data["sources"]["with_youtube"]).is_equal_to(1)
    assert_that(data["episodes"]["total_known"]).is_equal_to(3)
    assert_that(data["episodes"]["transcribed"]).is_equal_to(2)
    assert_that(data["episodes"]["extracted"]).is_equal_to(1)
    assert_that(data["pipelines"]["ingestion_runs_total"]).is_equal_to(3)
    assert_that(data["pipelines"]["ingestion_runs_in_progress"]).is_equal_to(1)
    assert_that(data["pipelines"]["ingestion_runs_failed"]).is_equal_to(1)
    assert_that(data["pipelines"]["ingestion_runs_completed"]).is_equal_to(1)
    assert_that(data["pipelines"]["transcription_jobs_pending"]).is_equal_to(1)
    assert_that(data["pipelines"]["transcription_jobs_failed"]).is_equal_to(1)
    assert_that(data["pipelines"]["projection_repairs_pending"]).is_zero()
    assert_that(data["pipelines"]["projection_repairs_failed"]).is_zero()
    assert_that(data["search"]["enabled"]).is_true()


def test_get_ops_operational_metrics_reports_review_projection_and_alert_health(
    client: TestClient,
    db_session: Session,
    sample_v2_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ops health metrics summarize throughput, lag, and delivery."""
    now = datetime.now(UTC)
    episode = db_session.query(Episode).filter(Episode.title == "Episode 1").one()
    media = db_session.query(Media).filter(Media.title == "Test Book").one()
    candidate = MentionCandidate(
        episode_id=episode.id,
        media_type=MediaType.BOOK.value,
        raw_title="Reviewed Book",
        confidence=0.9,
    )
    pending_candidate = MentionCandidate(
        episode_id=episode.id,
        media_type=MediaType.BOOK.value,
        raw_title="Pending Book",
        confidence=0.8,
    )
    db_session.add_all([candidate, pending_candidate])
    db_session.flush()
    db_session.add_all(
        [
            ReviewItem(
                mention_candidate_id=candidate.id,
                status=ReviewItemStatus.APPROVED.value,
                created_at=now - timedelta(minutes=20),
                decided_at=now - timedelta(minutes=5),
            ),
            ReviewItem(
                mention_candidate_id=pending_candidate.id,
                status=ReviewItemStatus.PENDING.value,
            ),
        ]
    )
    db_session.add_all(
        [
            SearchProjectionRepair(
                resource_type=SearchProjectionRepairResourceType.MEDIA.value,
                resource_id=media.id,
                status=SearchProjectionRepairStatus.PENDING.value,
                reason=SearchProjectionRepairReason.MANUAL_REINDEX.value,
                created_at=now - timedelta(minutes=15),
            ),
            SearchProjectionRepair(
                resource_type=SearchProjectionRepairResourceType.MEDIA.value,
                resource_id=media.id,
                status=SearchProjectionRepairStatus.FAILED.value,
                reason=SearchProjectionRepairReason.MANUAL_REINDEX.value,
            ),
        ]
    )
    user = AccountUser(email="metrics@example.com")
    db_session.add(user)
    db_session.flush()
    rule = AccountAlertRule(
        user_id=user.id,
        target_type="media",
        target_id=media.id,
        event_type="new_mention",
    )
    digest = AccountDigest(
        user_id=user.id,
        subject="Updates",
        body_text="One update",
        event_count=1,
        delivered_at=now,
    )
    db_session.add_all([rule, digest])
    db_session.flush()
    db_session.add_all(
        [
            AccountAlertEvent(
                rule_id=rule.id,
                previous_count=0,
                observed_count=1,
                digest_id=digest.id,
            ),
            AccountAlertEvent(
                rule_id=rule.id,
                previous_count=1,
                observed_count=2,
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v2/ops/metrics")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["review"]["decisions_last_24h"]).is_equal_to(1)
    assert_that(data["review"]["median_decision_minutes_last_24h"]).is_equal_to(15)
    assert_that(data["projection"]["pending_repairs"]).is_equal_to(1)
    assert_that(data["projection"]["failed_repairs"]).is_equal_to(1)
    assert_that(data["projection"]["oldest_pending_age_seconds"]).is_greater_than(0)
    assert_that(data["review"]["pending_items"]).is_equal_to(1)
    assert_that(data["alerts"]["generated_events_last_24h"]).is_equal_to(2)
    assert_that(data["alerts"]["delivered_events_last_24h"]).is_equal_to(1)
    assert_that(data["alerts"]["pending_events"]).is_equal_to(1)

    monkeypatch.setattr(
        v2_ops_api,
        "get_settings",
        lambda: Settings(
            ops_review_pending_alert_threshold=1,
            ops_projection_pending_alert_threshold=1,
            ops_projection_oldest_pending_minutes=1,
            ops_alert_delivery_pending_threshold=1,
        ),
    )
    alerts = client.get("/api/v2/ops/alerts")
    alert_keys = {alert["key"] for alert in alerts.json()["alerts"]}

    assert_that(alerts.status_code).is_equal_to(200)
    assert_that(alert_keys).contains(
        "review_backlog",
        "projection_backlog",
        "projection_age",
        "projection_failures",
        "delivery_backlog",
    )


def test_get_ops_operational_alerts_require_pending_repair_for_age_alert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid reporting stale projection age when no repair is pending."""
    monkeypatch.setattr(
        v2_ops_api,
        "get_settings",
        lambda: Settings(ops_projection_oldest_pending_minutes=0),
    )

    response = client.get("/api/v2/ops/alerts")
    alert_keys = {alert["key"] for alert in response.json()["alerts"]}

    assert_that(response.status_code).is_equal_to(200)
    assert_that(alert_keys).does_not_contain("projection_age")


def test_get_ops_pipeline_activity_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify the v2 ops pipeline activity endpoint exposes recent runs and jobs."""
    response = client.get("/api/v2/ops/pipelines?run_limit=2&job_limit=2")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["summary"]["ingestion_runs_total"]).is_equal_to(3)
    assert_that(data["summary"]["transcription_jobs_failed"]).is_equal_to(1)
    assert_that(data["summary"]["projection_repairs_pending"]).is_zero()
    assert_that(data["runs"]).is_length(2)
    assert_that(data["jobs"]).is_length(2)

    assert_that(data["runs"][0]["id"]).is_equal_to("run_3")
    assert_that(data["runs"][0]["status"]).is_equal_to("completed")
    assert_that(data["runs"][0]["duration_seconds"]).is_equal_to(600)
    assert_that(data["runs"][1]["id"]).is_equal_to("run_2")
    assert_that(data["runs"][1]["error_summary"]).is_equal_to(
        "Transcript provider failed"
    )

    assert_that(data["jobs"][0]["id"]).is_equal_to("job_2")
    assert_that(data["jobs"][0]["podcast_id"]).is_equal_to("pod_2")
    assert_that(data["jobs"][0]["podcast_slug"]).is_equal_to("another-podcast")
    assert_that(data["jobs"][0]["episode_title"]).is_equal_to("Another Episode 1")
    assert_that(data["jobs"][0]["duration_seconds"]).is_equal_to(120)
    assert_that(data["jobs"][1]["id"]).is_equal_to("job_1")
    assert_that(data["jobs"][1]["episode_id"]).is_equal_to("ep_2")
    assert_that(data["jobs"][1]["duration_seconds"]).is_none()


def test_run_ops_pipeline_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
    db_session: Session,
) -> None:
    """Verify the v2 ops pipeline run endpoint creates a tracked run."""
    response = client.post("/api/v2/ops/pipelines/run")

    assert_that(response.status_code).is_equal_to(202)
    data = response.json()
    assert_that(data["id"]).is_equal_to("run_4")
    assert_that(data["status"]).is_equal_to("in_progress")
    assert_that(data["duration_seconds"]).is_none()
    assert_that(db_session.query(IngestionRun).count()).is_equal_to(4)


def test_rerun_ops_episode_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
    db_session: Session,
) -> None:
    """Verify the v2 ops episode rerun endpoint queues selected jobs."""
    response = client.post(
        "/api/v2/ops/episodes/ep_1/rerun",
        json={"job_types": ["transcribe", "extract"]},
    )

    assert_that(response.status_code).is_equal_to(202)
    data = response.json()
    assert_that(data["episode_id"]).is_equal_to("ep_1")
    assert_that(data["jobs"]).is_length(2)
    assert_that(data["jobs"][0]["job_type"]).is_equal_to("transcribe")
    assert_that(data["jobs"][0]["status"]).is_equal_to("pending")
    assert_that(data["jobs"][1]["job_type"]).is_equal_to("extract")
    assert_that(data["jobs"][1]["podcast_id"]).is_equal_to("pod_1")

    episode = db_session.query(Episode).filter(Episode.id == 1).one()
    assert_that(episode.transcript_status).is_equal_to("pending")
    assert_that(episode.extraction_status).is_equal_to("pending")
    repairs = (
        db_session.query(SearchProjectionRepair)
        .order_by(SearchProjectionRepair.id.asc())
        .all()
    )
    assert_that(repairs).is_length(2)
    assert_that(repairs[0].resource_type).is_equal_to("episode")
    assert_that(repairs[0].resource_id).is_equal_to(1)
    assert_that(repairs[0].status).is_equal_to(
        SearchProjectionRepairStatus.PENDING.value,
    )
    assert_that(repairs[0].reason).is_equal_to(
        SearchProjectionRepairReason.EXTRACT_RERUN.value,
    )
    assert_that(repairs[1].resource_type).is_equal_to("media")
    assert_that(repairs[1].resource_id).is_equal_to(1)


def test_rerun_ops_episode_v2_not_found(client: TestClient) -> None:
    """Verify the v2 ops episode rerun endpoint returns 404 for bad IDs."""
    response = client.post(
        "/api/v2/ops/episodes/not-an-episode-id/rerun",
        json={"job_types": ["cleanup"]},
    )

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["detail"]).is_equal_to("Episode not found")


def test_get_ops_podcasts_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts returns opaque IDs, counts, and sources."""
    response = client.get("/api/v2/ops/podcasts?sort=mention_count&order=desc")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["total"]).is_equal_to(3)
    assert_that(data["items"]).is_length(3)
    assert_that(data["items"][0]["id"]).is_equal_to("pod_1")
    assert_that(data["items"][0]["slug"]).is_equal_to("test-podcast")
    assert_that(data["items"][0]["status"]).is_equal_to("active")
    assert_that(data["items"][0]["discovery_source"]).is_equal_to("rss")
    assert_that(data["items"][0]["episode_count"]).is_equal_to(2)
    assert_that(data["items"][0]["mention_count"]).is_equal_to(1)
    assert_that(data["items"][0]["sources"]["rss_url"]).contains("feed.xml")
    assert_that(data["items"][0]["sources"]["youtube_channel_id"]).is_equal_to(
        "channel-1"
    )


def test_get_ops_podcasts_v2_with_status_and_source_filters(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts applies status and source filters."""
    response = client.get("/api/v2/ops/podcasts?status=watchlist&source=spotify")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["total"]).is_equal_to(1)
    assert_that(data["items"]).is_length(1)
    assert_that(data["items"][0]["id"]).is_equal_to("pod_2")
    assert_that(data["items"][0]["status"]).is_equal_to("watchlist")
    assert_that(data["items"][0]["discovery_source"]).is_equal_to("spotify")
    assert_that(data["items"][0]["sources"]["spotify_id"]).is_equal_to("spotify-1")
    assert_that(data["items"][0]["sources"]["podscripts_slug"]).is_equal_to(
        "another-podcast"
    )


def test_create_ops_podcast_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts can create a new managed podcast."""
    response = client.post(
        "/api/v2/ops/podcasts",
        json={
            "name": "Fresh Podcast",
            "slug": "fresh-podcast",
            "status": "active",
            "description": "Newly added show",
            "cover_url": "https://example.com/fresh.jpg",
            "discovery_source": "manual",
            "sources": {
                "rss_url": "https://example.com/fresh.xml",
                "apple_id": "apple-42",
            },
        },
    )

    assert_that(response.status_code).is_equal_to(201)
    data = response.json()
    assert_that(data["id"]).is_equal_to("pod_4")
    assert_that(data["slug"]).is_equal_to("fresh-podcast")
    assert_that(data["status"]).is_equal_to("active")
    assert_that(data["discovery_source"]).is_equal_to("manual")
    assert_that(data["episode_count"]).is_equal_to(0)
    assert_that(data["mention_count"]).is_equal_to(0)
    assert_that(data["sources"]["rss_url"]).contains("fresh.xml")
    assert_that(data["sources"]["apple_id"]).is_equal_to("apple-42")


def test_create_ops_podcast_v2_duplicate_slug(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts rejects duplicate slugs."""
    response = client.post(
        "/api/v2/ops/podcasts",
        json={
            "name": "Duplicate Podcast",
            "slug": "test-podcast",
        },
    )

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).is_equal_to("Podcast slug already exists")


def test_update_ops_podcast_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts can update managed podcast fields."""
    response = client.patch(
        "/api/v2/ops/podcasts/pod_3",
        json={
            "status": "active",
            "description": "Paused no more",
            "discovery_source": "manual",
            "sources": {
                "rss_url": "https://example.com/paused.xml",
                "spotify_id": "spotify-3",
            },
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("pod_3")
    assert_that(data["status"]).is_equal_to("active")
    assert_that(data["description"]).is_equal_to("Paused no more")
    assert_that(data["discovery_source"]).is_equal_to("manual")
    assert_that(data["sources"]["rss_url"]).contains("paused.xml")
    assert_that(data["sources"]["spotify_id"]).is_equal_to("spotify-3")


def test_update_ops_podcast_v2_can_clear_fields(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcast updates can explicitly clear nullable fields."""
    response = client.patch(
        "/api/v2/ops/podcasts/pod_1",
        json={
            "description": None,
            "discovery_source": None,
            "sources": {
                "rss_url": None,
                "youtube_channel_id": None,
            },
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("pod_1")
    assert_that(data["description"]).is_none()
    assert_that(data["discovery_source"]).is_none()
    assert_that(data["sources"]["rss_url"]).is_none()
    assert_that(data["sources"]["youtube_channel_id"]).is_none()


def test_update_ops_podcast_v2_rejects_null_status(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcast updates reject null for non-clearable fields."""
    response = client.patch(
        "/api/v2/ops/podcasts/pod_1",
        json={"status": None},
    )

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.json()["detail"]).contains("status")


def test_archive_ops_podcast_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 ops podcasts supports a safe archive action."""
    response = client.post("/api/v2/ops/podcasts/pod_2/archive")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("pod_2")
    assert_that(data["status"]).is_equal_to("paused")


def test_archive_ops_podcast_v2_not_found(client: TestClient) -> None:
    """Verify v2 ops podcast archive returns 404 for bad opaque IDs."""
    response = client.post("/api/v2/ops/podcasts/not-a-podcast-id/archive")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["detail"]).is_equal_to("Podcast not found")


def test_update_ops_podcast_v2_not_found(
    client: TestClient,
) -> None:
    """Verify v2 ops podcast updates return 404 for bad opaque IDs."""
    response = client.patch(
        "/api/v2/ops/podcasts/not-a-podcast-id",
        json={"status": "active"},
    )

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["detail"]).is_equal_to("Podcast not found")


def test_get_admin_settings_v2(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the v2 admin settings endpoint exposes runtime configuration."""
    settings = SimpleNamespace(
        app_name="Podex",
        debug=False,
        api_key="super-secret",
        rate_limit_per_minute=100,
        cors_origins=["http://localhost:4321"],
        youtube_channel_id="channel-ops",
        meilisearch_enabled=True,
        meilisearch_url="http://search.internal:7700",
    )
    monkeypatch.setattr(v2_admin_api, "get_settings", lambda: settings)

    response = client.get("/api/v2/admin/settings")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["app_name"]).is_equal_to("Podex")
    assert_that(data["api_key_enabled"]).is_true()
    assert_that(data["rate_limit_per_minute"]).is_equal_to(100)
    assert_that(data["youtube_channel_id"]).is_equal_to("channel-ops")
    assert_that(data["meilisearch_url"]).contains("search.internal")


def test_update_admin_settings_v2(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the v2 admin settings endpoint applies partial runtime updates."""
    settings = SimpleNamespace(
        app_name="Podex",
        debug=False,
        api_key="",
        rate_limit_per_minute=100,
        cors_origins=["http://localhost:4321"],
        youtube_channel_id="",
        meilisearch_enabled=True,
        meilisearch_url="http://localhost:7700",
    )
    monkeypatch.setattr(v2_admin_api, "get_settings", lambda: settings)

    response = client.patch(
        "/api/v2/admin/settings",
        json={
            "debug": True,
            "rate_limit_per_minute": 250,
            "cors_origins": ["https://podex.dev", "https://ops.podex.dev"],
            "youtube_channel_id": "updated-channel",
            "meilisearch_enabled": False,
            "meilisearch_url": "http://search.internal:7700",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["debug"]).is_true()
    assert_that(data["rate_limit_per_minute"]).is_equal_to(250)
    assert_that(data["cors_origins"]).contains("https://podex.dev")
    assert_that(data["youtube_channel_id"]).is_equal_to("updated-channel")
    assert_that(data["meilisearch_enabled"]).is_false()
    assert_that(settings.debug).is_true()
    assert_that(settings.meilisearch_url).contains("search.internal")

    audit_response = client.get("/api/v2/ops/audit-log?resource_type=settings")

    assert_that(audit_response.status_code).is_equal_to(200)
    audit_data = audit_response.json()
    assert_that(audit_data["items"]).is_length(1)
    assert_that(audit_data["items"][0]["action"]).is_equal_to("update_settings")
    assert_that(audit_data["items"][0]["resource_id"]).is_equal_to("runtime")


def test_get_ops_review_queue_v2(
    client: TestClient,
    sample_review_queue_data: dict[str, Any],
) -> None:
    """Verify v2 ops review queue lists pending candidates with opaque IDs."""
    response = client.get("/api/v2/ops/review-queue?status=pending&priority=high")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["items"]).is_length(1)
    assert_that(data["items"][0]["id"]).is_equal_to("rev_1")
    assert_that(data["items"][0]["status"]).is_equal_to("pending")
    assert_that(data["items"][0]["priority"]).is_equal_to("high")
    assert_that(data["items"][0]["episode_id"]).is_equal_to("ep_1")
    assert_that(data["items"][0]["podcast_id"]).is_equal_to("pod_1")
    assert_that(data["items"][0]["candidate"]["id"]).is_equal_to("cand_1")
    assert_that(data["items"][0]["candidate"]["type"]).is_equal_to("book")
    assert_that(data["items"][0]["candidate"]["source_job_id"]).is_equal_to(
        "job_1",
    )
    assert_that(data["items"][0]["candidate"]["source_job_status"]).is_equal_to(
        JobStatus.COMPLETED.value,
    )
    assert_that(data["items"][0]["candidate"]["source_job_backend"]).is_equal_to(
        "anthropic",
    )
    assert_that(data["items"][0]["candidate"]["source_job_model"]).is_equal_to(
        "claude-sonnet-4-20250514",
    )
    assert_that(data["items"][0]["candidate"]["extraction_jobs"]).is_length(2)
    assert_that(data["items"][0]["candidate"]["extraction_jobs"][0]["id"]).is_equal_to(
        "job_2"
    )
    assert_that(
        data["items"][0]["candidate"]["extraction_jobs"][0]["status"]
    ).is_equal_to(JobStatus.FAILED.value)
    assert_that(
        data["items"][0]["candidate"]["extraction_jobs"][0]["error_message"]
    ).is_equal_to("provider rate limit")
    assert_that(
        data["items"][0]["candidate"]["extraction_jobs"][0]["is_source_job"]
    ).is_false()
    assert_that(data["items"][0]["candidate"]["extraction_jobs"][1]["id"]).is_equal_to(
        "job_1"
    )
    assert_that(
        data["items"][0]["candidate"]["extraction_jobs"][1]["is_source_job"]
    ).is_true()
    assert_that(data["items"][0]["candidate"]["provenance"]).is_length(1)
    assert_that(data["items"][0]["candidate"]["provenance"][0]["id"]).is_equal_to(
        "prov_1",
    )
    assert_that(
        data["items"][0]["candidate"]["provenance"][0]["event_type"]
    ).is_equal_to(MentionCandidateProvenanceEventType.CREATED.value)
    assert_that(
        data["items"][0]["candidate"]["provenance"][0]["source_job_id"]
    ).is_equal_to("job_1")
    assert_that(
        data["items"][0]["candidate"]["provenance"][0]["change_summary"]
    ).is_equal_to("Created from extraction result")
    assert_that(data["items"][0]["candidate"]["state"]).is_equal_to(
        MentionCandidateState.PENDING_REVIEW.value,
    )


def test_approve_ops_review_item_v2_publishes_candidate_and_logs_audit(
    client: TestClient,
    db_session: Session,
    sample_review_queue_data: dict[str, Any],
) -> None:
    """Verify approving a review item publishes a mention and writes an audit log."""
    response = client.post(
        "/api/v2/ops/review-queue/rev_1/approve",
        json={
            "actor_name": "operator-1",
            "note": "Looks good to publish",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["status"]).is_equal_to(ReviewItemStatus.APPROVED.value)
    assert_that(data["assigned_to"]).is_equal_to("operator-1")
    assert_that(data["candidate"]["state"]).is_equal_to(
        MentionCandidateState.PUBLISHED.value,
    )
    assert_that(data["candidate"]["media_id"]).starts_with("med_")
    assert_that(data["candidate"]["mention_id"]).starts_with("men_")

    candidate = (
        db_session.query(MentionCandidate)
        .filter(MentionCandidate.id == sample_review_queue_data["candidate"].id)
        .one()
    )
    assert_that(candidate.state).is_equal_to(MentionCandidateState.PUBLISHED.value)
    assert_that(candidate.mention_id).is_not_none()
    assert_that(db_session.query(Media).count()).is_equal_to(1)
    assert_that(db_session.query(Mention).count()).is_equal_to(1)
    assert_that(db_session.query(AuditLog).count()).is_equal_to(1)

    audit_response = client.get("/api/v2/ops/audit-log?resource_type=review_item")

    assert_that(audit_response.status_code).is_equal_to(200)
    audit_data = audit_response.json()
    assert_that(audit_data["items"]).is_length(1)
    assert_that(audit_data["items"][0]["action"]).is_equal_to(
        "approve_review_item",
    )
    assert_that(audit_data["items"][0]["resource_id"]).is_equal_to("rev_1")


def test_reclassify_ops_review_item_v2_updates_candidate_and_logs_audit(
    client: TestClient,
    db_session: Session,
    sample_review_queue_data: dict[str, Any],
) -> None:
    """Verify reclassifying a review item updates the pending candidate and audit log."""
    response = client.post(
        "/api/v2/ops/review-queue/rev_1/reclassify",
        json={
            "type": "movie",
            "raw_title": "The Reclassified Movie",
            "suggested_author": "Director Queue",
            "actor_name": "operator-3",
            "note": "Actually a film",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["assigned_to"]).is_equal_to("operator-3")
    assert_that(data["decision_note"]).is_equal_to("Actually a film")
    assert_that(data["candidate"]["type"]).is_equal_to("movie")
    assert_that(data["candidate"]["raw_title"]).is_equal_to(
        "The Reclassified Movie",
    )
    assert_that(data["candidate"]["suggested_author"]).is_equal_to(
        "Director Queue",
    )
    assert_that(data["candidate"]["state"]).is_equal_to(
        MentionCandidateState.PENDING_REVIEW.value,
    )
    assert_that(data["candidate"]["provenance"]).is_length(2)
    assert_that(data["candidate"]["provenance"][0]["change_summary"]).contains(
        "raw_title",
    )

    candidate = (
        db_session.query(MentionCandidate)
        .filter(MentionCandidate.id == sample_review_queue_data["candidate"].id)
        .one()
    )
    assert_that(candidate.media_type).is_equal_to(MediaType.MOVIE.value)
    assert_that(candidate.raw_title).is_equal_to("The Reclassified Movie")
    assert_that(candidate.suggested_author).is_equal_to("Director Queue")

    audit_response = client.get("/api/v2/ops/audit-log?resource_type=review_item")

    assert_that(audit_response.status_code).is_equal_to(200)
    audit_data = audit_response.json()
    assert_that(audit_data["items"]).is_length(1)
    assert_that(audit_data["items"][0]["action"]).is_equal_to(
        "reclassify_review_item",
    )


def test_split_ops_review_item_v2_creates_replacement_candidates_and_logs_audit(
    client: TestClient,
    db_session: Session,
    sample_review_queue_data: dict[str, Any],
) -> None:
    """Verify splitting a candidate creates auditable pending replacements."""
    response = client.post(
        "/api/v2/ops/review-queue/rev_1/split",
        json={
            "actor_name": "operator-4",
            "note": "Contains two distinct references",
            "candidates": [
                {"type": "book", "raw_title": "First Reference"},
                {
                    "type": "movie",
                    "raw_title": "Second Reference",
                    "suggested_author": "Second Director",
                },
            ],
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["original"]["status"]).is_equal_to(ReviewItemStatus.SPLIT.value)
    assert_that(data["original"]["candidate"]["state"]).is_equal_to(
        MentionCandidateState.SPLIT.value,
    )
    assert_that(data["items"]).is_length(2)
    assert_that(data["items"][0]["status"]).is_equal_to(ReviewItemStatus.PENDING.value)
    assert_that(data["items"][0]["candidate"]["raw_title"]).is_equal_to(
        "First Reference",
    )
    assert_that(data["items"][1]["candidate"]["type"]).is_equal_to("movie")
    assert_that(
        data["items"][1]["candidate"]["provenance"][0]["change_summary"]
    ).is_equal_to(
        "Split from review item 1",
    )

    original_candidate = (
        db_session.query(MentionCandidate)
        .filter(MentionCandidate.id == sample_review_queue_data["candidate"].id)
        .one()
    )
    assert_that(original_candidate.state).is_equal_to(MentionCandidateState.SPLIT.value)
    assert_that(db_session.query(ReviewItem).count()).is_equal_to(3)
    assert_that(db_session.query(Mention).count()).is_zero()

    audit_response = client.get("/api/v2/ops/audit-log?resource_type=review_item")
    assert_that(audit_response.status_code).is_equal_to(200)
    assert_that(audit_response.json()["items"][0]["action"]).is_equal_to(
        "split_review_item",
    )


def test_merge_ops_review_item_v2_uses_existing_media(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify merging a review item attaches it to an existing canonical media record."""
    podcast = Podcast(name="Merge Review Podcast", slug="merge-review", status="active")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Merge Review Episode",
        transcript_status="completed",
        extraction_status="completed",
    )
    target_media = Media(type=MediaType.BOOK.value, title="Canonical Book")
    db_session.add_all([episode, target_media])
    db_session.flush()

    candidate = MentionCandidate(
        episode_id=episode.id,
        media_type=MediaType.BOOK.value,
        raw_title="Canonical Book",
        confidence=0.66,
        context="Canonical Book was mentioned",
    )
    db_session.add(candidate)
    db_session.flush()

    review_item = ReviewItem(mention_candidate_id=candidate.id)
    db_session.add(review_item)
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/review-queue/rev_{review_item.id}/merge",
        json={
            "target_id": f"med_{target_media.id}",
            "actor_name": "operator-2",
            "note": "Matched existing media",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["status"]).is_equal_to(ReviewItemStatus.MERGED.value)
    assert_that(data["target_media_id"]).is_equal_to(f"med_{target_media.id}")
    assert_that(data["candidate"]["state"]).is_equal_to(
        MentionCandidateState.MERGED.value,
    )
    assert_that(data["candidate"]["media_id"]).is_equal_to(f"med_{target_media.id}")

    merged_candidate = (
        db_session.query(MentionCandidate)
        .filter(MentionCandidate.id == candidate.id)
        .one()
    )
    assert_that(merged_candidate.media_id).is_equal_to(target_media.id)
    assert_that(
        db_session.query(Mention).filter(Mention.media_id == target_media.id).count(),
    ).is_equal_to(1)


def test_merge_ops_media_v2(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify the v2 ops media merge endpoint moves mentions and fills gaps."""
    podcast = Podcast(name="Merge Podcast", slug="merge-podcast", status="active")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Merge Episode",
        transcript_status="completed",
        extraction_status="completed",
    )
    db_session.add(episode)
    db_session.flush()

    source_media = Media(
        type=MediaType.BOOK.value,
        title="Duplicate Book",
        author="Source Author",
        year=1999,
        description="Source description",
        google_books_id="source-book-id",
        metadata_json={"isbn_13": "9780000000001"},
        verification_sources=["google_books"],
        doi_verified=True,
    )
    target_media = Media(
        type=MediaType.BOOK.value,
        title="Duplicate Book",
    )
    db_session.add_all([source_media, target_media])
    db_session.flush()

    source_mention = Mention(
        episode_id=episode.id,
        media_id=source_media.id,
        timestamp_seconds=15,
        context="Source mention",
        confidence=0.91,
    )
    target_mention = Mention(
        episode_id=episode.id,
        media_id=target_media.id,
        timestamp_seconds=30,
        context="Target mention",
        confidence=0.88,
    )
    db_session.add_all([source_mention, target_mention])
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/media/med_{source_media.id}/merge",
        json={"target_id": f"med_{target_media.id}"},
    )

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["source_id"]).is_equal_to(f"med_{source_media.id}")
    assert_that(data["target"]["id"]).is_equal_to(f"med_{target_media.id}")
    assert_that(data["target"]["mention_count"]).is_equal_to(2)
    assert_that(data["target"]["author"]).is_equal_to("Source Author")
    assert_that(data["target"]["year"]).is_equal_to(1999)

    merged_media = db_session.query(Media).filter(Media.id == target_media.id).one()
    assert_that(merged_media.description).is_equal_to("Source description")
    assert_that(merged_media.google_books_id).is_equal_to("source-book-id")
    assert_that(merged_media.metadata_json).contains_key("isbn_13")
    assert_that(merged_media.verification_sources).contains("google_books")
    assert_that(merged_media.doi_verified).is_true()
    assert_that(
        db_session.query(Media).filter(Media.id == source_media.id).count(),
    ).is_zero()
    assert_that(
        db_session.query(Mention).filter(Mention.media_id == target_media.id).count(),
    ).is_equal_to(2)


def test_merge_ops_media_v2_rejects_same_target(
    client: TestClient,
    sample_v2_media_data: dict[str, Any],
) -> None:
    """Verify the v2 ops media merge endpoint rejects self-merges."""
    response = client.post(
        "/api/v2/ops/media/med_1/merge",
        json={"target_id": "med_1"},
    )

    assert_that(response.status_code).is_equal_to(400)
    assert_that(response.json()["detail"]).contains("must differ")


def test_get_public_trends_v2(
    client: TestClient,
    sample_v2_trends_data: dict[str, Any],
) -> None:
    """Verify v2 trends returns overview, by-type stats, and opaque IDs."""
    response = client.get("/api/v2/trends")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["overview"]["total_podcasts"]).is_equal_to(1)
    assert_that(data["overview"]["total_episodes"]).is_equal_to(1)
    assert_that(data["overview"]["total_media"]).is_equal_to(4)
    assert_that(data["overview"]["total_mentions"]).is_equal_to(4)
    assert_that(data["overview"]["total_books"]).is_equal_to(2)
    assert_that(data["overview"]["total_movies"]).is_equal_to(2)
    assert_that(data["by_type"]).is_length(3)
    assert_that(data["by_type"][0]["type"]).is_equal_to("book")
    assert_that(data["by_type"][0]["count"]).is_equal_to(2)
    assert_that(data["by_type"][0]["mention_count"]).is_equal_to(3)
    assert_that(data["top_mentioned"]).is_length(2)
    assert_that(data["top_mentioned"][0]["id"]).starts_with("med_")
    assert_that(data["top_mentioned"][0]["title"]).is_equal_to("Book One")
    assert_that(data["top_mentioned"][0]["mention_count"]).is_equal_to(3)


def test_get_public_trends_v2_with_type_filter_and_limit(
    client: TestClient,
    sample_v2_trends_data: dict[str, Any],
) -> None:
    """Verify v2 trends filters only the top-mentioned section."""
    response = client.get("/api/v2/trends?type=movie&limit=1")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["overview"]["total_media"]).is_equal_to(4)
    assert_that(data["by_type"]).is_length(3)
    assert_that(data["top_mentioned"]).is_length(1)
    assert_that(data["top_mentioned"][0]["type"]).is_equal_to("movie")
    assert_that(data["top_mentioned"][0]["title"]).is_equal_to("Movie One")


def test_list_public_media_v2(
    client: TestClient,
    sample_v2_media_data: dict[str, Any],
) -> None:
    """Verify v2 media listing returns opaque IDs and sorting metadata."""
    response = client.get("/api/v2/media?sort=mention_count&order=desc")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["items"]).is_length(3)
    assert_that(data["items"][0]["id"]).is_equal_to("med_1")
    assert_that(data["items"][0]["title"]).is_equal_to("The Great Gatsby")
    assert_that(data["items"][0]["mention_count"]).is_equal_to(2)


def test_get_public_media_detail_v2(
    client: TestClient,
    sample_v2_media_data: dict[str, Any],
) -> None:
    """Verify v2 media detail returns public enrichment and mention fields."""
    response = client.get("/api/v2/media/med_1")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("med_1")
    assert_that(data["title"]).is_equal_to("The Great Gatsby")
    assert_that(data["mentions"]).is_length(2)
    assert_that(data["google_books_id"]).is_equal_to("books-1")
    assert_that(data["mentions"][0]["id"]).starts_with("men_")
    assert_that(data["mentions"][0]["episode"]["id"]).is_equal_to("ep_1")


def test_public_detail_explanations_expose_ready_summaries_and_relation_provenance(
    client: TestClient,
    db_session: Session,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify public details project durable summaries and supporting relations."""
    media = db_session.query(Media).filter(Media.title == "Test Book").one()
    episode = db_session.query(Episode).filter(Episode.title == "Episode 1").one()
    related = Media(type=MediaType.MOVIE.value, title="Test Adaptation")
    db_session.add(related)
    db_session.flush()
    upsert_media_summary(
        db=db_session,
        media=media,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="summary-v1",
        source_model="model-v1",
        source_text_hash=stable_source_text_hash("media explanation"),
        summary_text="A recurring recommendation in conversations about craft.",
    )
    upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="summary-v1",
        source_model="model-v1",
        source_text_hash=stable_source_text_hash("episode explanation"),
        summary_text="The guest recommends a book for new readers.",
    )
    upsert_media_relation(
        db=db_session,
        subject_media_id=media.id,
        object_media_id=related.id,
        relation_type=MediaRelationType.ADAPTED_FROM,
        source="episode_extraction",
        provenance_episode_id=episode.id,
        confidence=0.91,
        evidence_text="The adaptation follows this book.",
    )
    db_session.commit()

    media_detail = client.get("/api/v2/media/med_1")
    episode_detail = client.get("/api/v2/episodes/ep_1")

    assert_that(media_detail.status_code).is_equal_to(200)
    assert_that(media_detail.json()["derivative_summary"]).contains(
        "recurring recommendation",
    )
    relation = media_detail.json()["related_media"][0]
    assert_that(relation["id"]).is_equal_to(f"med_{related.id}")
    assert_that(relation["relation_type"]).is_equal_to("adapted_from")
    assert_that(relation["direction"]).is_equal_to("outgoing")
    assert_that(relation["provenance_episode_id"]).is_equal_to("ep_1")
    assert_that(episode_detail.status_code).is_equal_to(200)
    assert_that(episode_detail.json()["derivative_summary"]).contains(
        "recommends a book",
    )
    assert_that(episode_detail.json()["derivative_mentioned_media_titles"]).contains(
        "Test Book",
    )


def test_get_public_media_mentions_v2(
    client: TestClient,
    sample_v2_media_data: dict[str, Any],
) -> None:
    """Verify v2 media mentions endpoint returns nested episode references."""
    response = client.get("/api/v2/media/med_1/mentions")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data).is_length(2)
    assert_that(data[0]["episode"]["id"]).is_equal_to("ep_1")
    assert_that(data[0]["youtube_timestamp_url"]).contains("youtube.com")


def test_get_public_media_not_found_v2(client: TestClient) -> None:
    """Verify v2 media routes return 404 for invalid opaque IDs."""
    response = client.get("/api/v2/media/not-a-media-id")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["detail"]).is_equal_to("Media not found")


class FakeSearchClient:
    """Minimal search client stub for API tests."""

    def __init__(self, *, results: list[dict[str, Any]], enabled: bool = True) -> None:
        self._results = results
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        """Return whether search is enabled."""
        return self._enabled

    def multi_search(self, queries: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the predefined grouped search payload.

        Args:
            queries: Search queries requested by the API.

        Returns:
            Fake grouped search results.
        """
        assert_that(queries).is_length(3)
        return {"results": self._results}

    def search(
        self,
        index_name: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return an empty single-index search payload.

        Args:
            index_name: Index name.
            query: Search query text.
            limit: Requested limit.
            offset: Requested offset.
            filters: Optional filters.
            sort: Optional sort rules.

        Returns:
            Empty search response.
        """
        del index_name, query, limit, offset, filters, sort
        return {"hits": [], "processingTimeMs": 0}


@pytest.fixture
def fake_global_search_client() -> FakeSearchClient:
    """Provide a deterministic fake search client for v2 search tests."""
    return FakeSearchClient(
        results=[
            {
                "indexUid": "media",
                "processingTimeMs": 12,
                "estimatedTotalHits": 1,
                "hits": [
                    {
                        "id": 7,
                        "title": "The Great Gatsby",
                        "type": "book",
                        "author": "F. Scott Fitzgerald",
                        "year": 1925,
                        "cover_url": "https://example.com/gatsby.jpg",
                    }
                ],
            },
            {
                "indexUid": "episodes",
                "processingTimeMs": 8,
                "estimatedTotalHits": 1,
                "hits": [
                    {
                        "id": 11,
                        "title": "Books Episode",
                        "podcast_name": "Media Podcast",
                        "episode_number": 3,
                        "thumbnail_url": "https://example.com/episode.jpg",
                    }
                ],
            },
            {
                "indexUid": "podcasts",
                "processingTimeMs": 5,
                "estimatedTotalHits": 1,
                "hits": [
                    {
                        "id": 5,
                        "name": "Media Podcast",
                        "slug": "media-podcast",
                        "episode_count": 24,
                        "cover_url": "https://example.com/podcast.jpg",
                    }
                ],
            },
        ]
    )


def test_search_public_catalog_v2(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    fake_global_search_client: FakeSearchClient,
) -> None:
    """Verify v2 grouped search returns opaque identifiers per resource type."""
    monkeypatch.setattr(
        v2_public_api,
        "get_search_client",
        lambda: fake_global_search_client,
    )

    response = client.get("/api/v2/search?q=gatsby&limit=3")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["query"]).is_equal_to("gatsby")
    assert_that(data["processing_time_ms"]).is_equal_to(25)
    assert_that(data["results"]).is_length(3)

    media_group = next(group for group in data["results"] if group["type"] == "media")
    episode_group = next(
        group for group in data["results"] if group["type"] == "episode"
    )
    podcast_group = next(
        group for group in data["results"] if group["type"] == "podcast"
    )

    assert_that(media_group["hits"][0]["id"]).is_equal_to("med_7")
    assert_that(media_group["hits"][0]["url"]).is_equal_to("/media/med_7")
    assert_that(episode_group["hits"][0]["id"]).is_equal_to("ep_11")
    assert_that(episode_group["hits"][0]["url"]).is_equal_to("/episodes/ep_11")
    assert_that(podcast_group["hits"][0]["id"]).is_equal_to("pod_5")
    assert_that(podcast_group["hits"][0]["url"]).is_equal_to("/podcasts/media-podcast")
    event = db_session.query(SearchAnalyticsEvent).one()
    assert_that(event.query).is_equal_to("gatsby")
    assert_that(event.result_count).is_equal_to(3)


def test_public_selection_and_ops_search_analytics_support_relevance_review(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify anonymous selections and query aggregations are reviewable in ops."""
    db_session.add_all(
        [
            SearchAnalyticsEvent(
                event_type="query",
                query="no results",
                result_count=0,
                processing_time_ms=3,
            ),
            SearchAnalyticsEvent(
                event_type="query",
                query="gatsby",
                result_count=3,
                processing_time_ms=4,
            ),
        ],
    )
    db_session.commit()

    selected = client.post(
        "/api/v2/search/selection",
        json={
            "query": "  Gatsby ",
            "result_type": "media",
            "result_id": "med_7",
        },
    )
    summary = client.get("/api/v2/ops/search/analytics")

    assert_that(selected.status_code).is_equal_to(200)
    assert_that(selected.json()["recorded"]).is_true()
    assert_that(summary.status_code).is_equal_to(200)
    assert_that(summary.json()["searches"]).is_equal_to(2)
    assert_that(summary.json()["zero_result_searches"]).is_equal_to(1)
    assert_that(summary.json()["selections"]).is_equal_to(1)
    gatsby = next(
        item for item in summary.json()["queries"] if item["query"] == "gatsby"
    )
    assert_that(gatsby["selections"]).is_equal_to(1)


def test_search_public_catalog_v2_when_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify v2 grouped search returns an empty payload when search is disabled."""
    monkeypatch.setattr(
        v2_public_api,
        "get_search_client",
        lambda: FakeSearchClient(results=[], enabled=False),
    )

    response = client.get("/api/v2/search?q=gatsby")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["results"]).is_empty()
    assert_that(data["processing_time_ms"]).is_equal_to(0)


def test_public_editorial_collections_expose_published_ordered_media_only(
    client: TestClient,
    db_session: Session,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify curated discovery exposes published collection records by slug."""
    media = db_session.query(Media).filter(Media.title == "Test Book").one()
    published = EditorialCollection(
        slug="essential-books",
        title="Essential Books",
        description="Books repeatedly discussed across the catalog.",
        curator_name="Podex Editors",
        published=True,
        featured=True,
    )
    hidden = EditorialCollection(
        slug="draft-list",
        title="Draft List",
        description="Not ready.",
        published=False,
    )
    db_session.add_all([published, hidden])
    db_session.flush()
    db_session.add(
        EditorialCollectionItem(
            collection_id=published.id,
            media_id=media.id,
            position=1,
        ),
    )
    db_session.commit()

    listed = client.get("/api/v2/collections")
    detail = client.get("/api/v2/collections/essential-books")
    hidden_detail = client.get("/api/v2/collections/draft-list")

    assert_that(listed.status_code).is_equal_to(200)
    assert_that(listed.json()).is_length(1)
    assert_that(listed.json()[0]["slug"]).is_equal_to("essential-books")
    assert_that(listed.json()[0]["item_count"]).is_equal_to(1)
    assert_that(detail.status_code).is_equal_to(200)
    assert_that(detail.json()["items"][0]["id"]).is_equal_to("med_1")
    assert_that(hidden_detail.status_code).is_equal_to(404)


def test_list_public_episodes_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 episode listing returns opaque identifiers and stats."""
    response = client.get("/api/v2/episodes?page=1&per_page=10")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["items"]).is_length(3)
    assert_that(data["items"][0]["id"]).is_equal_to("ep_3")
    assert_that(data["items"][0]["podcast_id"]).is_equal_to("pod_2")
    assert_that(data["items"][0]["mention_count"]).is_equal_to(0)


def test_list_public_episodes_v2_with_podcast_filter(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 episode listing accepts an opaque podcast filter."""
    response = client.get("/api/v2/episodes?podcast_id=pod_1")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["items"]).is_length(2)
    assert_that(data["items"][0]["id"]).is_equal_to("ep_2")
    assert_that(data["items"][1]["id"]).is_equal_to("ep_1")


def test_get_public_episode_detail_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 episode detail returns podcast context and nested media mentions."""
    response = client.get("/api/v2/episodes/ep_1")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["id"]).is_equal_to("ep_1")
    assert_that(data["podcast_id"]).is_equal_to("pod_1")
    assert_that(data["podcast_slug"]).is_equal_to("test-podcast")
    assert_that(data["transcript_status"]).is_equal_to("completed")
    assert_that(data["extraction_status"]).is_equal_to("completed")
    assert_that(data["cleanup_status"]).is_equal_to("pending")
    assert_that(data["mentions"]).is_length(1)
    assert_that(data["mentions"][0]["media"]["id"]).is_equal_to("med_1")
    assert_that(data["mentions"][0]["media"]["title"]).is_equal_to("Test Book")


def test_get_public_episode_mentions_v2(
    client: TestClient,
    sample_v2_data: dict[str, Any],
) -> None:
    """Verify v2 episode mentions return opaque media identifiers."""
    response = client.get("/api/v2/episodes/ep_1/mentions")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data).is_length(1)
    assert_that(data[0]["id"]).starts_with("men_")
    assert_that(data[0]["media"]["id"]).is_equal_to("med_1")
    assert_that(data[0]["youtube_timestamp_url"]).contains("youtube.com")


def test_get_public_episode_not_found_v2(client: TestClient) -> None:
    """Verify v2 episode routes return 404 for invalid opaque IDs."""
    response = client.get("/api/v2/episodes/not-an-episode-id")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.json()["detail"]).is_equal_to("Episode not found")


class FakeSearchProjectionClient:
    """Minimal search projection client stub for ops API tests."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        healthy: bool = True,
        stats: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._enabled = enabled
        self._healthy = healthy
        self._stats = stats or {}

    @property
    def enabled(self) -> bool:
        """Return whether the projection client is enabled."""
        return self._enabled

    def health_check(self) -> bool:
        """Return the configured health state."""
        return self._healthy

    def get_index_stats(self, index_name: str) -> dict[str, Any]:
        """Return predefined stats for a search index.

        Args:
            index_name: Search index name.

        Returns:
            Fake per-index stats payload.
        """
        return self._stats.get(index_name, {})


class RecordingSearchMutationClient:
    """Record search projection mutation calls for API tests."""

    def __init__(self, *, fail_add: bool = False, fail_delete: bool = False) -> None:
        self.fail_add = fail_add
        self.fail_delete = fail_delete
        self.added_documents: list[tuple[str, list[dict[str, Any]]]] = []
        self.deleted_documents: list[tuple[str, int | str]] = []
        self.updated_settings: list[tuple[str, dict[str, Any]]] = []

    def add_documents(
        self,
        index_name: str,
        documents: list[dict[str, Any]],
        primary_key: str = "id",
    ) -> dict[str, Any]:
        """Record added documents or raise a configured failure.

        Args:
            index_name: Search index name.
            documents: Added documents.
            primary_key: Primary key field name.

        Returns:
            Fake task response.

        Raises:
            RuntimeError: When configured to simulate an indexing failure.
        """
        del primary_key
        if self.fail_add:
            raise RuntimeError("search add failed")

        self.added_documents.append((index_name, documents))
        return {"status": "enqueued"}

    def delete_document(
        self,
        index_name: str,
        document_id: int | str,
    ) -> dict[str, Any]:
        """Record deleted documents or raise a configured failure.

        Args:
            index_name: Search index name.
            document_id: Deleted document identifier.

        Returns:
            Fake task response.

        Raises:
            RuntimeError: When configured to simulate an indexing failure.
        """
        if self.fail_delete:
            raise RuntimeError("search delete failed")

        self.deleted_documents.append((index_name, document_id))
        return {"status": "enqueued"}

    def search(
        self,
        index_name: str,
        query: str,
        limit: int = 10,
        offset: int = 0,
        filters: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return deterministic baseline results for tuning previews."""
        del offset, filters, sort
        return {"hits": [{"title": f"{query} baseline", "index": index_name}][:limit]}

    def update_index_settings(
        self,
        index_name: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        """Record applied search tuning settings."""
        self.updated_settings.append((index_name, settings))
        return {"status": "enqueued", "task_uid": 42}


def test_get_ops_search_projection_v2(
    client: TestClient,
    db_session: Session,
    sample_v2_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify v2 ops search projection returns per-index stats."""
    del sample_v2_data
    fake_client = FakeSearchProjectionClient(
        stats={
            "media": {"numberOfDocuments": 12, "isIndexing": False},
            "episodes": {"numberOfDocuments": 34, "isIndexing": True},
            "podcasts": {"numberOfDocuments": 5, "isIndexing": False},
        }
    )
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: fake_client)
    monkeypatch.setattr(
        v2_ops_api,
        "get_settings",
        lambda: SimpleNamespace(meilisearch_enabled=True),
    )
    db_session.add(
        SearchProjectionRepair(
            resource_type="media",
            resource_id=7,
            status=SearchProjectionRepairStatus.PENDING.value,
            reason=SearchProjectionRepairReason.EXTRACT_RERUN.value,
        )
    )
    db_session.commit()

    response = client.get("/api/v2/ops/search")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["configured"]).is_true()
    assert_that(data["healthy"]).is_true()
    assert_that(data["indexes"]).is_length(3)
    assert_that(data["indexes"][0]["name"]).is_equal_to("media")
    assert_that(data["indexes"][0]["document_count"]).is_equal_to(12)
    assert_that(data["indexes"][1]["name"]).is_equal_to("episodes")
    assert_that(data["indexes"][1]["is_indexing"]).is_true()
    assert_that(data["indexes"][2]["name"]).is_equal_to("podcasts")
    assert_that(data["indexes"][2]["document_count"]).is_equal_to(5)
    assert_that(data["repair_summary"]["pending"]).is_equal_to(1)
    assert_that(data["repair_summary"]["failed"]).is_zero()
    assert_that(data["repairs"]).is_length(1)
    assert_that(data["repairs"][0]["resource_type"]).is_equal_to("media")
    assert_that(data["repairs"][0]["resource_id"]).is_equal_to("med_7")
    assert_that(data["repairs"][0]["status"]).is_equal_to("pending")


def test_get_ops_search_projection_v2_when_disabled(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify v2 ops search projection reports disabled configuration cleanly."""
    monkeypatch.setattr(
        v2_ops_api,
        "get_search_client",
        lambda: FakeSearchProjectionClient(enabled=False, healthy=False),
    )
    monkeypatch.setattr(
        v2_ops_api,
        "get_settings",
        lambda: SimpleNamespace(meilisearch_enabled=False),
    )

    response = client.get("/api/v2/ops/search")

    assert_that(response.status_code).is_equal_to(200)
    data = response.json()
    assert_that(data["configured"]).is_false()
    assert_that(data["healthy"]).is_false()
    assert_that(data["indexes"]).is_length(3)
    assert_that(data["indexes"][0]["document_count"]).is_equal_to(0)
    assert_that(data["indexes"][1]["document_count"]).is_equal_to(0)
    assert_that(data["indexes"][2]["document_count"]).is_equal_to(0)
    assert_that(data["repair_summary"]["pending"]).is_zero()
    assert_that(data["repairs"]).is_empty()


def test_queue_ops_search_reindex_v2_scopes_repairs_and_records_audit(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify manually requested reindex work is scoped to source and media type."""
    matching_podcast = Podcast(name="Books Podcast", slug="books-podcast")
    other_podcast = Podcast(name="Movies Podcast", slug="movies-podcast")
    db_session.add_all([matching_podcast, other_podcast])
    db_session.flush()
    matching_episode = Episode(podcast_id=matching_podcast.id, title="Book Episode")
    other_episode = Episode(podcast_id=other_podcast.id, title="Movie Episode")
    book = Media(type=MediaType.BOOK.value, title="Indexed Book")
    movie = Media(type=MediaType.MOVIE.value, title="Indexed Movie")
    db_session.add_all([matching_episode, other_episode, book, movie])
    db_session.flush()
    db_session.add_all(
        [
            Mention(episode_id=matching_episode.id, media_id=book.id),
            Mention(episode_id=other_episode.id, media_id=movie.id),
        ]
    )
    db_session.commit()

    response = client.post(
        "/api/v2/ops/search/reindex",
        json={
            "resource_type": "all",
            "podcast_id": f"pod_{matching_podcast.id}",
            "media_type": "book",
            "actor_name": "operator",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()).contains_entry({"media_queued": 1})
    assert_that(response.json()).contains_entry({"episodes_queued": 1})
    repairs = db_session.query(SearchProjectionRepair).all()
    assert_that(repairs).is_length(2)
    assert_that({repair.reason for repair in repairs}).is_equal_to({"manual_reindex"})
    audit = client.get("/api/v2/ops/audit-log?resource_type=search_projection").json()
    assert_that(audit["items"][0]["action"]).is_equal_to("reindex_search")


def test_preview_and_apply_ops_search_tuning_v2(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify operators can review baseline hits then apply audited settings."""
    search_client = RecordingSearchMutationClient()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)
    payload = {
        "index": "media",
        "query": "sci fi",
        "synonyms": {"sci fi": ["science fiction"]},
        "ranking_rules": ["words", "exactness", "mention_count:desc"],
        "actor_name": "operator",
    }

    preview = client.post("/api/v2/ops/search/tuning/preview", json=payload)
    applied = client.post("/api/v2/ops/search/tuning", json=payload)

    assert_that(preview.status_code).is_equal_to(200)
    assert_that(preview.json()["sample_hits"][0]["title"]).is_equal_to(
        "sci fi baseline"
    )
    assert_that(applied.status_code).is_equal_to(200)
    assert_that(applied.json()["task_uid"]).is_equal_to(42)
    assert_that(search_client.updated_settings).is_length(1)
    assert_that(search_client.updated_settings[0][1]["synonyms"]).contains_entry(
        {"sci fi": ["science fiction"]},
    )


def test_recalculate_ops_retention_sampling_v2_reports_and_audits_policy(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify a versioned calibration policy persists coverage and audit data."""
    podcast = Podcast(name="Calibration Podcast", slug="calibration-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Calibration Episode")
    db_session.add(episode)
    db_session.flush()
    db_session.add(
        MentionCandidate(
            episode_id=episode.id,
            media_type="book",
            raw_title="Calibration Book",
            confidence=0.95,
        )
    )
    db_session.add(
        Transcript(
            episode_id=episode.id,
            provider="rss",
            raw_text="calibration transcript",
            fetched_at=datetime(2026, 5, 20, tzinfo=UTC),
        )
    )
    db_session.commit()

    response = client.post(
        "/api/v2/ops/retention/sampling/recalculate",
        json={
            "policy_version": "calibration-v2",
            "sample_rate": 0.1,
            "actor_name": "operator",
        },
    )
    persisted = client.get("/api/v2/ops/retention/sampling")

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["policy_version"]).is_equal_to("calibration-v2")
    assert_that(response.json()["sampled_count"]).is_equal_to(1)
    assert_that(persisted.json()["strata"][0]["topic"]).is_equal_to("book")
    audit = client.get(
        "/api/v2/ops/audit-log?resource_type=retention_sampling_policy"
    ).json()
    assert_that(audit["items"][0]["action"]).is_equal_to("update_retention_sampling")


def test_ops_transcript_retention_preview_evaluate_and_purge_preserves_digest(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify raw purge is dry-runnable, coverage-gated, audited, and provable."""
    podcast = Podcast(name="Retention Ops Podcast", slug="retention-ops")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Retention Ops Episode")
    media = Media(type=MediaType.BOOK.value, title="Retention Book")
    db_session.add_all([episode, media])
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="rss",
        raw_text="A durable raw transcript mentioning Retention Book.",
        cleaned_text="A durable raw transcript mentioning Retention Book.",
        fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
        digest_text="Processing proof summary.",
        digest_created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    db_session.add(transcript)
    db_session.flush()
    db_session.add_all(
        [
            Mention(episode_id=episode.id, media_id=media.id),
            MentionCandidate(
                episode_id=episode.id,
                media_type="book",
                raw_title="Retention Book",
                confidence=0.98,
            ),
            SemanticChunk(
                episode_id=episode.id,
                transcript_id=transcript.id,
                chunk_key="retention-ops-chunk",
                chunk_index=0,
                pipeline_version="chunks-v1",
                source_text_hash=stable_source_text_hash(transcript.cleaned_text or ""),
                text="Retention Book evidence.",
                context_snippet="Retention Book",
                token_count=3,
            ),
        ]
    )
    upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="summary-v1",
        source_model="model-v1",
        source_text_hash=stable_source_text_hash("episode"),
        summary_text="Episode derivative.",
    )
    upsert_media_summary(
        db=db_session,
        media=media,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="summary-v1",
        source_model="model-v1",
        source_text_hash=stable_source_text_hash("media"),
        summary_text="Media derivative.",
    )
    upsert_graph_triple(
        db=db_session,
        payload=GraphTripleInputData(
            subject_media_id=media.id,
            predicate="topic",
            object_value="retention",
            provenance_episode_id=episode.id,
            source="test",
        ),
    )
    db_session.commit()
    policy = {
        "policy_version": "retention-lifecycle-v1",
        "hot_days": 1,
        "warm_days": 2,
        "min_purge_confidence": 0.85,
        "actor_name": "operator",
    }

    preview = client.post(
        f"/api/v2/ops/retention/transcripts/trn_{transcript.id}/preview",
        json=policy,
    )
    assert_that(preview.status_code).is_equal_to(200)
    assert_that(preview.json()["purge_eligible"]).is_true()
    assert_that(transcript.retention_tier).is_equal_to("hot")

    evaluated = client.post(
        f"/api/v2/ops/retention/transcripts/trn_{transcript.id}/evaluate",
        json=policy,
    )
    purged = client.post(
        f"/api/v2/ops/retention/transcripts/trn_{transcript.id}/purge",
        json=policy,
    )

    assert_that(evaluated.json()["transcript"]["tier"]).is_equal_to("cold")
    assert_that(purged.status_code).is_equal_to(200)
    assert_that(purged.json()["transcript"]["has_raw_payload"]).is_false()
    assert_that(purged.json()["digest"]["summary_text"]).is_equal_to(
        "Processing proof summary."
    )
    assert_that(transcript.raw_text).is_none()
    saved_policy = db_session.query(TranscriptSourceRetentionPolicy).one()
    assert_that(saved_policy.source_key).is_equal_to(transcript.provider)
    assert_that(saved_policy.policy_version).is_equal_to("retention-lifecycle-v1")
    audit = client.get("/api/v2/ops/audit-log?resource_type=transcript").json()
    assert_that([item["action"] for item in audit["items"]]).contains(
        "evaluate_transcript_retention",
        "purge_transcript",
        "update_transcript_retention_policy",
    )


def test_ops_transcript_purge_blocks_incomplete_derivative_coverage(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify raw text remains stored when public-safe derivatives are missing."""
    podcast = Podcast(name="Blocked Retention Podcast", slug="blocked-retention")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Blocked Episode")
    db_session.add(episode)
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="rss",
        raw_text="Raw content still needed until derivatives exist.",
        retention_tier="cold",
        purge_eligible_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    db_session.add(transcript)
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/retention/transcripts/trn_{transcript.id}/purge",
        json={"policy_version": "retention-lifecycle-v1"},
    )

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.json()["detail"]).contains("Derivative coverage")
    assert_that(transcript.raw_text).is_not_none()


def test_ops_transcript_reacquire_creates_audited_hot_encrypted_asset(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify a purged transcript can be explicitly re-acquired for reprocessing."""

    class ReacquisitionStore:
        """Return encrypted-object metadata for the endpoint boundary test."""

        def put_json(
            self,
            *,
            storage_key: str,
            payload: dict[str, object],
        ) -> StoredTranscriptArtifactData:
            """Return successful private storage metadata."""
            return StoredTranscriptArtifactData(
                storage_key=storage_key,
                storage_backend="encrypted_filesystem",
                encryption_key_id="key-v1",
                byte_size=128,
            )

        def delete(self, *, storage_key: str) -> None:
            """No-op delete required by the storage port."""

    class ReacquisitionAcquirer:
        """Provide a deterministic replacement transcript."""

        def acquire(self, episode: Episode) -> TranscriptAcquisitionResult:
            """Return a fresh raw transcript payload."""
            return TranscriptAcquisitionResult(
                success=True,
                result=TranscriptResult(
                    provider="podscripts.co",
                    raw_text="A re-acquired transcript payload.",
                    segments=[
                        {"start": 0, "text": "A re-acquired transcript payload."}
                    ],
                    fetched_at=datetime(2026, 5, 24, tzinfo=UTC),
                ),
                source="podscripts.co",
            )

        def close(self) -> None:
            """Release no resources for the deterministic test provider."""

    podcast = Podcast(name="Reacquisition Podcast", slug="reacquisition-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Purged Episode")
    db_session.add(episode)
    db_session.flush()
    purged_at = datetime(2026, 5, 23, tzinfo=UTC)
    purged = Transcript(
        episode_id=episode.id,
        provider="podscripts.co",
        raw_text=None,
        retention_tier="purged",
        purged_at=purged_at,
    )
    db_session.add(purged)
    db_session.flush()
    digest = TranscriptDigest(
        transcript_id=purged.id,
        episode_id=episode.id,
        digest_key=f"transcript:{purged.id}:purge:proof",
        source_text_hash="proof",
        provider=purged.provider,
        summary_text="prior proof",
        generated_at=purged_at,
        purged_at=purged_at,
    )
    db_session.add(digest)
    db_session.commit()
    client.app.dependency_overrides[v2_ops_api.get_ops_transcript_artifact_store] = (
        lambda: ReacquisitionStore()
    )
    client.app.dependency_overrides[v2_ops_api.get_ops_transcript_acquirer] = lambda: (
        ReacquisitionAcquirer()
    )

    response = client.post(
        f"/api/v2/ops/retention/transcripts/trn_{purged.id}/reacquire",
        json={"actor_name": "operator", "note": "new model run"},
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["transcript"]["tier"]).is_equal_to("hot")
    assert_that(response.json()["transcript"]["has_raw_payload"]).is_true()
    assert_that(response.json()["transcript"]["has_stored_artifact"]).is_true()
    assert_that(response.json()["prior_digest_id"]).is_equal_to(f"digest_{digest.id}")
    artifact = db_session.query(TranscriptArtifact).one()
    fresh_transcript = (
        db_session.query(Transcript).filter(Transcript.id != purged.id).one()
    )
    assert_that(fresh_transcript.raw_text).is_none()
    assert_that(fresh_transcript.segments_json).is_none()
    assert_that(artifact.reacquired_from_digest_id).is_equal_to(digest.id)
    audit = client.get("/api/v2/ops/audit-log?resource_type=transcript").json()
    assert_that(audit["items"][0]["action"]).is_equal_to("reacquire_transcript")


def test_public_takedown_submission_can_be_reviewed_and_decided_in_ops(
    client: TestClient,
    db_session: Session,
) -> None:
    """Verify public takedown intake produces an auditable privileged case."""
    podcast = Podcast(name="Claimed Podcast", slug="claimed-podcast")
    db_session.add(podcast)
    db_session.commit()

    submitted = client.post(
        "/api/v2/takedown-requests",
        json={
            "subject_type": "podcast",
            "subject_id": f"pod_{podcast.id}",
            "requester_type": "rights_holder",
            "requester_name": "Rights Holder",
            "requester_email": "rights@example.com",
            "basis": "I control distribution rights for this catalog source.",
            "requested_actions": [
                "suppress_raw_transcript",
                "purge_search_projection",
            ],
        },
    )

    assert_that(submitted.status_code).is_equal_to(201)
    assert_that(submitted.json()["status"]).is_equal_to("pending")
    case = db_session.query(TakedownRequest).one()
    assert_that(submitted.json()["id"]).is_equal_to(f"td_{case.id}")

    queued = client.get("/api/v2/ops/takedown-requests?status=pending")
    assert_that(queued.status_code).is_equal_to(200)
    assert_that(queued.json()["items"][0]["requester_email"]).is_equal_to(
        "rights@example.com"
    )

    decided = client.post(
        f"/api/v2/ops/takedown-requests/td_{case.id}/decision",
        json={
            "status": "approved",
            "actor_name": "operator",
            "note": "Ownership evidence verified; suppression execution pending.",
        },
    )

    assert_that(decided.status_code).is_equal_to(200)
    assert_that(decided.json()["status"]).is_equal_to("approved")
    assert_that(decided.json()["decided_by"]).is_equal_to("operator")
    audit = client.get("/api/v2/ops/audit-log?resource_type=takedown_request").json()
    assert_that([item["action"] for item in audit["items"]]).contains(
        "submit_takedown_request",
        "decide_takedown_request",
    )


def test_approved_creator_takedown_executes_suppression_and_source_opt_out(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify approved creator actions remove payloads and register opt-out."""

    class DeletingArtifactStore:
        """Record artifact deletions initiated by approved suppression."""

        def __init__(self) -> None:
            self.deleted: list[str] = []

        def delete(self, *, storage_key: str) -> None:
            """Record one encrypted artifact deletion."""
            self.deleted.append(storage_key)

    podcast = Podcast(name="Creator Podcast", slug="creator-podcast")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Claimed Episode")
    media = Media(type=MediaType.BOOK.value, title="Claimed Book")
    db_session.add_all([episode, media])
    db_session.flush()
    transcript = Transcript(
        episode_id=episode.id,
        provider="rss",
        cleaned_text="Creator discusses Claimed Book.",
        digest_text="Suppression proof.",
        digest_created_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    db_session.add(transcript)
    db_session.flush()
    artifact = TranscriptArtifact(
        transcript_id=transcript.id,
        episode_id=episode.id,
        storage_key="private/transcript.json.enc",
        storage_backend="filesystem",
        encryption_key_id="test-key",
        provider=transcript.provider,
        source_text_hash="source-hash",
        content_type="application/json",
        byte_size=123,
        stored_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    mention = Mention(episode_id=episode.id, media_id=media.id, context="Claimed Book")
    chunk = SemanticChunk(
        episode_id=episode.id,
        transcript_id=transcript.id,
        chunk_key="claimed-chunk",
        chunk_index=0,
        pipeline_version="chunks-v1",
        source_text_hash="source-hash",
        text="Claimed Book",
        context_snippet="Claimed Book",
        token_count=2,
    )
    db_session.add_all([artifact, mention, chunk])
    db_session.flush()
    db_session.add(
        MentionCandidate(
            episode_id=episode.id,
            media_id=media.id,
            mention_id=mention.id,
            media_type=MediaType.BOOK.value,
            raw_title="Claimed Book",
            confidence=0.99,
            state=MentionCandidateState.PUBLISHED.value,
        ),
    )
    upsert_episode_summary(
        db=db_session,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="derivatives-v1",
        prompt_version="summary-v1",
        source_model="model-v1",
        source_text_hash=stable_source_text_hash("Claimed Episode"),
        summary_text="Public episode summary.",
    )
    db_session.commit()
    search_client = RecordingSearchMutationClient()
    artifact_store = DeletingArtifactStore()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)
    client.app.dependency_overrides[v2_ops_api.get_ops_transcript_artifact_store] = (
        lambda: artifact_store
    )

    submitted = client.post(
        "/api/v2/takedown-requests",
        json={
            "subject_type": "podcast",
            "subject_id": f"pod_{podcast.id}",
            "requester_type": "creator",
            "requester_name": "Creator",
            "requester_email": "creator@example.com",
            "basis": "I created and own this podcast feed and its recordings.",
            "requested_actions": [
                "suppress_raw_transcript",
                "suppress_derivatives",
                "unpublish_mentions",
                "purge_search_projection",
                "register_source_opt_out",
            ],
        },
    )
    decided = client.post(
        f"/api/v2/ops/takedown-requests/{submitted.json()['id']}/decision",
        json={
            "status": "approved",
            "actor_name": "operator",
            "note": "Verified creator identity and control of the feed.",
        },
    )

    assert_that(decided.status_code).is_equal_to(200)
    assert_that(artifact_store.deleted).is_equal_to(["private/transcript.json.enc"])
    assert_that(db_session.query(Mention).count()).is_zero()
    assert_that(db_session.query(SemanticChunk).count()).is_zero()
    assert_that(db_session.query(EpisodeSummary).count()).is_zero()
    db_session.refresh(transcript)
    db_session.refresh(artifact)
    assert_that(transcript.cleaned_text).is_none()
    assert_that(transcript.purged_at).is_not_none()
    assert_that(artifact.purged_at).is_not_none()
    policy = db_session.query(TranscriptSourceRetentionPolicy).one()
    assert_that(policy.source_retention_opt_out).is_true()
    assert_that(search_client.deleted_documents).contains(
        ("episodes", episode.id),
    )
    case = db_session.query(TakedownRequest).one()
    assert_that(case.metadata_json).contains_entry({"mentions_unpublished": 1})
    assert_that(case.metadata_json).contains_entry({"source_opt_outs_registered": 1})


def test_approve_ops_review_item_v2_syncs_search_projection(
    client: TestClient,
    sample_review_queue_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify approval syncs media and episode projection documents."""
    del sample_review_queue_data
    search_client = RecordingSearchMutationClient()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)

    response = client.post(
        "/api/v2/ops/review-queue/rev_1/approve",
        json={"actor_name": "operator-1", "note": "Looks good"},
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(search_client.added_documents).is_length(2)
    assert_that(search_client.added_documents[0][0]).is_equal_to("media")
    assert_that(search_client.added_documents[0][1][0]).contains_entry({"id": 1})
    assert_that(search_client.added_documents[0][1][0]).contains_entry(
        {"mention_count": 1},
    )
    assert_that(search_client.added_documents[1][0]).is_equal_to("episodes")
    assert_that(search_client.added_documents[1][1][0]).contains_entry({"id": 1})
    assert_that(search_client.added_documents[1][1][0]).contains_entry(
        {"mention_count": 1},
    )


def test_approve_ops_review_item_v2_survives_projection_sync_failure(
    client: TestClient,
    db_session: Session,
    sample_review_queue_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify approval still succeeds when projection sync fails."""
    del sample_review_queue_data
    failing_client = RecordingSearchMutationClient(fail_add=True)
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: failing_client)

    response = client.post(
        "/api/v2/ops/review-queue/rev_1/approve",
        json={"actor_name": "operator-1", "note": "Looks good"},
    )

    assert_that(response.status_code).is_equal_to(200)
    candidate = (
        db_session.query(MentionCandidate).filter(MentionCandidate.id == 1).one()
    )
    assert_that(candidate.state).is_equal_to(MentionCandidateState.PUBLISHED.value)
    repairs = (
        db_session.query(SearchProjectionRepair)
        .order_by(SearchProjectionRepair.id.asc())
        .all()
    )
    assert_that(repairs).is_length(2)
    assert_that(repairs[0].status).is_equal_to(
        SearchProjectionRepairStatus.FAILED.value,
    )
    assert_that(repairs[1].status).is_equal_to(
        SearchProjectionRepairStatus.FAILED.value,
    )


def test_merge_ops_media_v2_syncs_search_projection(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify media merge refreshes the target projection and deletes the source."""
    search_client = RecordingSearchMutationClient()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)

    podcast = Podcast(name="Projection Merge Podcast", slug="projection-merge")
    db_session.add(podcast)
    db_session.flush()

    episode = Episode(
        podcast_id=podcast.id,
        title="Projection Merge Episode",
        transcript_status="completed",
        extraction_status="completed",
    )
    source_media = Media(type=MediaType.BOOK.value, title="Source Book")
    target_media = Media(type=MediaType.BOOK.value, title="Target Book")
    db_session.add_all([episode, source_media, target_media])
    db_session.flush()

    db_session.add(
        Mention(
            episode_id=episode.id,
            media_id=source_media.id,
            timestamp_seconds=10,
            context="Source Book",
            confidence=0.9,
        )
    )
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/media/med_{source_media.id}/merge",
        json={"target_id": f"med_{target_media.id}"},
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(search_client.added_documents).is_length(1)
    assert_that(search_client.added_documents[0][0]).is_equal_to("media")
    assert_that(search_client.added_documents[0][1][0]).contains_entry(
        {"id": target_media.id},
    )
    assert_that(search_client.deleted_documents).contains(("media", source_media.id))


def test_update_ops_media_v2_syncs_search_projection(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify metadata corrections refresh the media search document."""
    search_client = RecordingSearchMutationClient()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)
    media = Media(type=MediaType.BOOK.value, title="Working Title")
    db_session.add(media)
    db_session.commit()

    response = client.patch(
        f"/api/v2/ops/media/med_{media.id}",
        json={"title": "Canonical Title"},
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(search_client.added_documents).is_length(1)
    assert_that(search_client.added_documents[0][1][0]).contains_entry(
        {"id": media.id},
    )
    assert_that(search_client.added_documents[0][1][0]).contains_entry(
        {"title": "Canonical Title"},
    )


def test_split_ops_media_v2_syncs_both_media_projections(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify split recovery refreshes both affected media search documents."""
    search_client = RecordingSearchMutationClient()
    monkeypatch.setattr(v2_ops_api, "get_search_client", lambda: search_client)
    podcast = Podcast(name="Split Projection Podcast", slug="split-projection")
    db_session.add(podcast)
    db_session.flush()
    episode = Episode(podcast_id=podcast.id, title="Split Projection Episode")
    source = Media(type=MediaType.BOOK.value, title="Combined Record")
    db_session.add_all([episode, source])
    db_session.flush()
    mention = Mention(episode_id=episode.id, media_id=source.id)
    db_session.add(mention)
    db_session.commit()

    response = client.post(
        f"/api/v2/ops/media/med_{source.id}/split",
        json={
            "mention_ids": [f"men_{mention.id}"],
            "type": "book",
            "title": "Recovered Record",
        },
    )

    assert_that(response.status_code).is_equal_to(200)
    projected = [documents[0] for _, documents in search_client.added_documents]
    assert_that(projected).is_length(2)
    assert_that(projected[0]).contains_entry({"id": source.id})
    assert_that(projected[0]).contains_entry({"mention_count": 0})
    assert_that(projected[1]).contains_entry({"title": "Recovered Record"})
    assert_that(projected[1]).contains_entry({"mention_count": 1})


def test_legacy_v1_surface_is_retired(client: TestClient) -> None:
    """Ensure removed public v1 endpoints are not mounted again."""
    response = client.get("/api/v1/media")

    assert_that(response.status_code).is_equal_to(404)
