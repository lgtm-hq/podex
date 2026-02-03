"""Tests for version 2 API endpoints."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.v2 import admin as v2_admin_api
from podex.api.v2 import ops as v2_ops_api
from podex.api.v2 import public as v2_public_api
from podex.models import (
    AuditLog,
    Episode,
    IngestionRun,
    JobStatus,
    JobType,
    Media,
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
    SearchProjectionRepair,
    SearchProjectionRepairReason,
    SearchProjectionRepairStatus,
    TranscriptionJob,
)


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
