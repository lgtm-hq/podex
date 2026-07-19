"""Tests for takedown intake, decision, and coordinated suppression."""

from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import Settings
from podex.models import (
    Mention,
    MentionCandidate,
    SemanticChunk,
    Transcript,
)
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.conftest import seed_catalog_graph

_OPS_HEADERS = {"X-Ops-Key": "test-ops-key"}


def _enable_ops(client: TestClient) -> None:
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key="test-ops-key",
    )


def _submit(client: TestClient, episode_id: int) -> int:
    response = client.post(
        "/api/v2/takedowns",
        json={
            "subject_type": "episode",
            "subject_id": episode_id,
            "requester_type": "creator",
            "requester_name": "The Creator",
            "requester_email": "creator@example.com",
            "basis": "This is my content and I request its removal.",
            "requested_actions": [
                "suppress_raw_transcript",
                "suppress_derivatives",
                "unpublish_mentions",
                "register_source_opt_out",
            ],
        },
    )
    if response.status_code != 202:  # pragma: no cover - guard
        raise AssertionError(response.text)
    return int(response.json()["id"])


def test_public_intake_accepts_and_audits(
    client: TestClient,
    db_session: Session,
) -> None:
    """Anonymous submissions create pending cases and audit entries."""
    graph = seed_catalog_graph(db_session)

    request_id = _submit(client, graph.episode_id)

    assert_that(request_id).is_greater_than(0)
    _enable_ops(client)
    listed = client.get("/api/v2/ops/takedown-requests", headers=_OPS_HEADERS)
    assert_that(listed.json()).is_length(1)
    assert_that(listed.json()[0]["status"]).is_equal_to("pending")
    audit = client.get("/api/v2/ops/audit-log", headers=_OPS_HEADERS)
    assert_that(audit.json()["items"][0]["action"]).is_equal_to(
        "submit_takedown_request",
    )


def test_rejection_leaves_content_untouched(
    client: TestClient,
    db_session: Session,
) -> None:
    """Rejected cases record the decision without suppressing content."""
    graph = seed_catalog_graph(db_session)
    request_id = _submit(client, graph.episode_id)
    _enable_ops(client)

    decided = client.post(
        f"/api/v2/ops/takedown-requests/{request_id}/decide",
        json={"status": "rejected", "note": "Insufficient ownership evidence."},
        headers=_OPS_HEADERS,
    )

    assert_that(decided.status_code).is_equal_to(200)
    assert_that(decided.json()["request"]["status"]).is_equal_to("rejected")
    assert_that(decided.json()["execution"]).is_none()
    assert_that(db_session.query(Mention).count()).is_equal_to(1)

    repeat = client.post(
        f"/api/v2/ops/takedown-requests/{request_id}/decide",
        json={"status": "approved", "note": "Changed my mind."},
        headers=_OPS_HEADERS,
    )
    assert_that(repeat.status_code).is_equal_to(409)
    assert_that(
        client.post(
            "/api/v2/ops/takedown-requests/999999/decide",
            json={"status": "rejected", "note": "n/a"},
            headers=_OPS_HEADERS,
        ).status_code,
    ).is_equal_to(404)


def test_approval_executes_coordinated_suppression(
    client: TestClient,
    db_session: Session,
) -> None:
    """Approval purges raw text, derivatives, and mentions, and opts out."""
    graph = seed_catalog_graph(db_session)
    transcript = Transcript(
        episode_id=graph.episode_id,
        provider="podscripts",
        raw_text="one two three four five six",
        cleaned_text="one two three four five six",
    )
    db_session.add(transcript)
    db_session.flush()
    sync_semantic_chunks_for_transcript(
        db=db_session,
        transcript=transcript,
        policy=SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2),
    )
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_type="book",
        raw_title="Dune",
        confidence=0.9,
        extraction_source="llm",
        mention_id=graph.mention_id,
    )
    db_session.add(candidate)
    db_session.commit()
    request_id = _submit(client, graph.episode_id)
    _enable_ops(client)

    decided = client.post(
        f"/api/v2/ops/takedown-requests/{request_id}/decide",
        json={
            "status": "approved",
            "actor_name": "operator",
            "note": "Ownership verified.",
        },
        headers=_OPS_HEADERS,
    )

    assert_that(decided.status_code).is_equal_to(200)
    execution = decided.json()["execution"]
    assert_that(execution["transcripts_suppressed"]).is_equal_to(1)
    assert_that(execution["mentions_unpublished"]).is_equal_to(1)
    assert_that(execution["source_opt_outs_registered"]).is_equal_to(1)
    assert_that(execution["derivatives_suppressed"]).is_greater_than(0)

    db_session.expire_all()
    stored = db_session.query(Transcript).one()
    assert_that(stored.raw_text).is_none()
    assert_that(stored.cleaned_text).is_none()
    assert_that(stored.purged_at).is_not_none()
    assert_that(stored.source_retention_opt_out).is_true()
    assert_that(db_session.query(Mention).count()).is_equal_to(0)
    assert_that(db_session.query(SemanticChunk).count()).is_equal_to(0)
    surviving = db_session.query(MentionCandidate).one()
    assert_that(surviving.mention_id).is_none()
