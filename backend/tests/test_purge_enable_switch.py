"""Transcript purge authorization across services, API, and scheduled work."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import RetentionSettings, Settings, get_settings
from podex.models import (
    Base,
    MentionCandidate,
    ScheduledWorkItemModel,
    TakedownRequest,
    Transcript,
    TranscriptArtifact,
    TranscriptDigest,
)
from podex.services.derivative_coverage import evaluate_episode_derivative_coverage
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.graph_relations import GraphTripleInputData
from podex.services.ops_retention_commands import purge_ops_transcript
from podex.services.recurring_notifications import (
    RETENTION_SCHEDULE_KEY,
    reconcile_notification_schedules,
    run_due_retention_work,
)
from podex.services.scheduled_work import plan_due_scheduled_work
from podex.services.transcript_retention_commands import (
    PURGE_DISABLED_REASON,
    create_transcript_digest,
    purge_transcript_if_eligible,
)
from tests.conftest import seed_catalog_graph
from tests.test_derivative_generation import FakeSummaryProvider
from tests.test_transcript_artifacts import RecordingArtifactStore

_NOW = datetime(2026, 11, 1, tzinfo=UTC)
_HEADERS = {"X-Ops-Key": "purge-switch-test-key"}


@pytest.fixture(name="purge_asset")
def purge_asset_fixture(
    db_session: Session,
) -> tuple[Transcript, RecordingArtifactStore]:
    """Persist an eligible transcript with coverage, an artifact, and a digest."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="original private source text",
        cleaned_text="clean private source text",
        segments_json=[{"start": 0, "text": "original private source text"}],
        fetched_at=_NOW - timedelta(days=400),
        digest_text="retained processing summary",
        digest_created_at=_NOW - timedelta(days=300),
        purge_eligible_at=_NOW - timedelta(days=1),
    )
    db_session.add(transcript)
    db_session.flush()
    store = RecordingArtifactStore()
    stored = store.put_json(
        storage_key="transcripts/purge-switch/raw.json.enc",
        payload={"raw_text": transcript.raw_text, "segments": transcript.segments_json},
    )
    db_session.add(
        TranscriptArtifact(
            transcript_id=transcript.id,
            episode_id=transcript.episode_id,
            storage_key=stored.storage_key,
            storage_backend=stored.storage_backend,
            encryption_key_id=stored.encryption_key_id,
            byte_size=stored.byte_size,
            provider=transcript.provider,
            source_text_hash="existing-artifact-hash",
            content_type="application/json",
            stored_at=_NOW,
        ),
    )
    db_session.add(
        MentionCandidate(
            episode_id=graph.episode_id,
            media_type="book",
            raw_title="Dune",
            confidence=0.95,
            extraction_source="llm",
            mention_id=graph.mention_id,
        ),
    )
    generate_episode_derivatives(
        db=db_session,
        episode=transcript.episode,
        transcript=transcript,
        summary_provider=FakeSummaryProvider(),
        graph_triples=[
            GraphTripleInputData(
                subject_media_id=graph.media_id,
                predicate="mentioned_context",
                object_value="source context",
                source="purge-switch-test",
                provenance_episode_id=graph.episode_id,
            ),
        ],
    )
    create_transcript_digest(db=db_session, transcript=transcript, purged_at=_NOW)
    db_session.commit()
    coverage = evaluate_episode_derivative_coverage(
        db=db_session, episode=transcript.episode
    )
    assert_that(coverage.purge_safe).is_true()
    return transcript, store


def _snapshot(db: Session) -> dict[str, list[dict[str, Any]]]:
    """Capture every stored row, including JSON values, to detect side effects."""
    db.flush()
    return {
        table.name: deepcopy(
            [dict(row) for row in db.execute(table.select()).mappings()]
        )
        for table in Base.metadata.sorted_tables
    }


def _takedown(db: Session, transcript: Transcript) -> TakedownRequest:
    """Persist a request covering every content-removal action."""
    request = TakedownRequest(
        subject_type="episode",
        subject_id=transcript.episode_id,
        requester_type="creator",
        requester_name="Creator",
        requester_email="creator@example.com",
        basis="Remove this source",
        requested_actions_json=[
            "suppress_raw_transcript",
            "suppress_derivatives",
            "unpublish_mentions",
            "register_source_opt_out",
        ],
        status="pending",
    )
    db.add(request)
    db.commit()
    return request


def _configure_api(client: TestClient, *, enabled: bool) -> None:
    """Set one app's explicit purge authorization without altering defaults."""
    app = client.app
    if not isinstance(app, FastAPI):
        raise AssertionError("Expected a FastAPI test app")
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_HEADERS["X-Ops-Key"],
        retention=RetentionSettings(purge_enabled=enabled),
    )


@pytest.mark.parametrize("path", ["service", "ops"])
@pytest.mark.parametrize("has_digest", [False, True])
def test_disabled_purge_services_leave_every_row_and_object_unchanged(
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
    path: str,
    has_digest: bool,
) -> None:
    """Default-off authorization rejects eligible purges before any write."""
    transcript, store = purge_asset
    if not has_digest:
        db_session.query(TranscriptDigest).delete(synchronize_session=False)
    db_session.commit()
    before = _snapshot(db_session)
    objects = deepcopy(store.objects)

    with pytest.raises(ValueError, match="PODEX_RETENTION__PURGE_ENABLED"):
        if path == "service":
            purge_transcript_if_eligible(
                db=db_session, transcript=transcript, artifact_store=store
            )
        else:
            purge_ops_transcript(
                db=db_session, transcript_id=transcript.id, artifact_store=store
            )

    db_session.commit()
    assert_that(_snapshot(db_session)).is_equal_to(before)
    assert_that(store.objects).is_equal_to(objects)
    assert_that(store.deleted).is_empty()


def test_disabled_purge_api_returns_problem_without_content_changes(
    client: TestClient,
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
) -> None:
    """Retention deletion rejects with a setting-named problem."""
    transcript, store = purge_asset
    _configure_api(client, enabled=False)
    before = _snapshot(db_session)
    objects = deepcopy(store.objects)

    response = client.post(
        f"/api/v2/ops/retention/{transcript.id}/purge", headers=_HEADERS
    )

    assert_that(response.status_code).is_equal_to(409)
    assert_that(response.headers["content-type"]).is_equal_to(
        "application/problem+json"
    )
    assert_that(response.json()["detail"]).is_equal_to(PURGE_DISABLED_REASON)
    assert_that(response.text).does_not_contain(_HEADERS["X-Ops-Key"])
    assert_that(_snapshot(db_session)).is_equal_to(before)
    assert_that(store.objects).is_equal_to(objects)


def test_disabled_retention_purge_does_not_block_approved_takedown(
    client: TestClient,
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Takedown still deletes raw and cleaned content with retention purge off."""
    transcript, store = purge_asset
    request = _takedown(db_session, transcript)
    _configure_api(client, enabled=False)
    monkeypatch.setattr(
        "podex.api.v2.ops.build_transcript_artifact_store", lambda **_kwargs: store
    )

    refused = client.post(
        f"/api/v2/ops/retention/{transcript.id}/purge", headers=_HEADERS
    )
    assert_that(refused.status_code).is_equal_to(409)
    assert_that(refused.json()["detail"]).is_equal_to(PURGE_DISABLED_REASON)
    assert_that(store.objects).is_not_empty()

    approved = client.post(
        f"/api/v2/ops/takedown-requests/{request.id}/decide",
        json={"status": "approved", "note": "Verified"},
        headers=_HEADERS,
    )

    assert_that(approved.status_code).is_equal_to(200)
    assert_that(approved.json()["execution"]["transcripts_suppressed"]).is_equal_to(1)
    db_session.expire_all()
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.cleaned_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(transcript.purged_at).is_not_none()
    assert_that(db_session.query(TranscriptArtifact).one().purged_at).is_not_none()
    assert_that(store.objects).is_empty()
    assert_that(store.deleted).is_equal_to(["transcripts/purge-switch/raw.json.enc"])


@pytest.mark.parametrize("enabled", [False, True])
def test_retention_preview_reports_switch_without_changes(
    client: TestClient,
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
    enabled: bool,
) -> None:
    """A safe eligible preview is blocked only when purge is disabled."""
    transcript, _store = purge_asset
    _configure_api(client, enabled=enabled)
    before = _snapshot(db_session)
    response = client.get(
        f"/api/v2/ops/retention/{transcript.id}/preview", headers=_HEADERS
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["derivative_coverage_ready"]).is_true()
    assert_that(response.json()["decision"]["purge_eligible"]).is_equal_to(enabled)
    assert_that(response.json()["decision"]["purge_blockers"]).is_equal_to(
        [] if enabled else [PURGE_DISABLED_REASON]
    )
    assert_that(_snapshot(db_session)).is_equal_to(before)


def test_enabled_ops_purge_preserves_existing_deletion_behavior(
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
) -> None:
    """Explicit opt-in deletes payloads and keeps the existing proof digest."""
    transcript, store = purge_asset
    digest_before = _snapshot(db_session)["transcript_digests"]
    result = purge_ops_transcript(
        db=db_session,
        transcript_id=transcript.id,
        artifact_store=store,
        retention=RetentionSettings(purge_enabled=True),
        now=_NOW,
    )
    db_session.commit()

    assert_that(result).is_not_none()
    assert_that(transcript.raw_text).is_none()
    assert_that(transcript.cleaned_text).is_none()
    assert_that(transcript.segments_json).is_none()
    assert_that(transcript.retention_tier).is_equal_to("purged")
    assert_that(store.deleted).is_equal_to(["transcripts/purge-switch/raw.json.enc"])
    assert_that(store.objects).is_empty()
    assert_that(db_session.query(TranscriptArtifact).one().purged_at).is_not_none()
    assert_that(_snapshot(db_session)["transcript_digests"]).is_equal_to(digest_before)
    assert_that(db_session.query(TranscriptDigest).count()).is_equal_to(1)


def test_enabled_purge_api_uses_environment_opt_in(
    client: TestClient,
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route passes its app settings through to the guarded storage command."""
    transcript, store = purge_asset
    _configure_api(client, enabled=True)
    monkeypatch.setattr(
        "podex.api.v2.ops.build_transcript_artifact_store", lambda **_kwargs: store
    )

    response = client.post(
        f"/api/v2/ops/retention/{transcript.id}/purge", headers=_HEADERS
    )

    assert_that(response.status_code).is_equal_to(200)
    assert_that(response.json()["transcript"]["tier"]).is_equal_to("purged")
    assert_that(db_session.query(Transcript).one().raw_text).is_none()
    assert_that(store.objects).is_empty()


@pytest.mark.parametrize("enabled", [False, True])
def test_scheduled_retention_evaluates_without_purging(
    db_session: Session,
    purge_asset: tuple[Transcript, RecordingArtifactStore],
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    """Sweeps still evaluate, retain content, and record disabled authorization."""
    transcript, store = purge_asset
    monkeypatch.setattr(get_settings().retention, "purge_enabled", enabled)
    reconcile_notification_schedules(
        db=db_session,
        digest_interval_minutes=60,
        retention_interval_minutes=60,
        now=_NOW,
    )
    plan_due_scheduled_work(db=db_session, now=_NOW)
    db_session.commit()
    before = _snapshot(db_session)
    objects = deepcopy(store.objects)

    results = run_due_retention_work(db=db_session, now=_NOW)
    db_session.commit()

    assert_that(results).is_length(1)
    assert_that(results[0].transcripts_evaluated).is_equal_to(1)
    reason = None if enabled else PURGE_DISABLED_REASON
    assert_that(results[0].purge_blocker).is_equal_to(reason)
    work = (
        db_session.query(ScheduledWorkItemModel)
        .filter(ScheduledWorkItemModel.schedule_key == RETENTION_SCHEDULE_KEY)
        .one()
    )
    assert_that((work.metadata_json or {}).get("purge_blocker")).is_equal_to(reason)
    assert_that(transcript.retention_evaluated_at).is_not_none()
    after = _snapshot(db_session)
    for table, rows in before.items():
        if table not in {"transcripts", "scheduled_work_items"}:
            assert_that(after[table]).is_equal_to(rows)
    for column in (
        "raw_text",
        "cleaned_text",
        "segments_json",
        "digest_text",
        "digest_created_at",
        "purged_at",
    ):
        assert_that(after["transcripts"][0][column]).is_equal_to(
            before["transcripts"][0][column]
        )
    assert_that(store.objects).is_equal_to(objects)
    assert_that(store.deleted).is_empty()
