"""Tests for takedown intake, decision, and coordinated suppression."""

from collections.abc import Callable
from unittest.mock import Mock

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.deps import get_app_settings
from podex.config import Settings
from podex.models import (
    ContentSuppressedError,
    ContentSuppression,
    ContentSuppressionScope,
    DerivativeGenerationRun,
    Episode,
    EpisodeSummary,
    Media,
    MediaType,
    Mention,
    MentionCandidate,
    SemanticChunk,
    TakedownSubjectType,
    Transcript,
)
from podex.services import takedown_requests
from podex.services.derivative_generation import generate_episode_derivatives
from podex.services.semantic_chunks import (
    SemanticChunkingPolicy,
    sync_semantic_chunks_for_transcript,
)
from tests.conftest import seed_catalog_graph
from tests.takedown_support import (
    approve_takedown,
    take_down_episode,
    take_down_mention,
)

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


def _suppression_rows(db: Session) -> list[tuple[str, str, int | None, int | None]]:
    """Return ``(subject_type, scope, episode_id, media_id)`` for every row."""
    db.expire_all()
    return [
        (row.subject_type, row.scope, row.episode_id, row.media_id)
        for row in db.query(ContentSuppression).order_by(ContentSuppression.id)
    ]


def _seed_episode_derivatives(db: Session, episode_id: int) -> Transcript:
    """Persist a transcript and its semantic chunks for one episode."""
    transcript = Transcript(
        episode_id=episode_id,
        provider="podscripts",
        cleaned_text="one two three four five six",
    )
    db.add(transcript)
    db.flush()
    sync_semantic_chunks_for_transcript(
        db=db,
        transcript=transcript,
        policy=SemanticChunkingPolicy(max_words=6, overlap_words=0, min_words=2),
    )
    db.commit()
    return transcript


def test_derivatives_are_not_regenerated_after_takedown(
    db_session: Session,
) -> None:
    """A derivative re-run after the takedown writes nothing and calls no provider."""
    graph = seed_catalog_graph(db_session)
    transcript = _seed_episode_derivatives(db_session, graph.episode_id)
    take_down_episode(db_session, graph.episode_id, "suppress_derivatives")
    db_session.query(Transcript).filter(Transcript.id == transcript.id).update(
        {Transcript.raw_text: "one two three four five six"},
    )
    db_session.commit()
    provider = Mock()
    episode = db_session.query(Episode).filter(Episode.id == graph.episode_id).one()

    with pytest.raises(ContentSuppressedError) as refused:
        generate_episode_derivatives(
            db=db_session,
            episode=episode,
            summary_provider=provider,
            embedding_provider=provider,
        )

    assert_that(refused.value.scope).is_equal_to(ContentSuppressionScope.DERIVATIVES)
    assert_that(refused.value.episode_id).is_equal_to(graph.episode_id)
    db_session.rollback()
    assert_that(provider.mock_calls).is_empty()
    assert_that(
        db_session.query(SemanticChunk)
        .filter(SemanticChunk.episode_id == graph.episode_id)
        .count(),
    ).is_zero()
    assert_that(db_session.query(EpisodeSummary).count()).is_zero()
    assert_that(db_session.query(DerivativeGenerationRun).count()).is_zero()


def test_suppression_rows_written_per_requested_scope(db_session: Session) -> None:
    """Each subject records one row per suppression scope it requested."""
    graph = seed_catalog_graph(db_session)
    other = Media(type=MediaType.MOVIE, title="Arrival")
    db_session.add(other)
    db_session.flush()
    mention = Mention(episode_id=graph.episode_id, media_id=other.id)
    db_session.add(mention)
    db_session.commit()

    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=mention.id,
        actions=["unpublish_mentions", "suppress_derivatives"],
    )
    approve_takedown(
        db_session,
        subject_type=TakedownSubjectType.PODCAST,
        subject_id=graph.podcast_id,
        actions=["unpublish_mentions", "suppress_raw_transcript"],
    )
    take_down_episode(db_session, graph.episode_id, "suppress_raw_transcript")

    assert_that(_suppression_rows(db_session)).is_equal_to(
        [
            ("mention_pair", "mentions", graph.episode_id, other.id),
            ("episode", "derivatives", graph.episode_id, None),
            ("podcast", "mentions", None, None),
        ],
    )
    keys = [row.suppression_key for row in db_session.query(ContentSuppression)]
    assert_that(keys).contains(
        f"mention_pair:{graph.episode_id}:{other.id}:mentions",
        f"podcast:{graph.podcast_id}:mentions",
    )


def test_repeated_takedown_of_one_subject_keeps_one_row(db_session: Session) -> None:
    """A second approval of the same subject and scope adds no duplicate row."""
    graph = seed_catalog_graph(db_session)
    for _ in range(2):
        take_down_episode(db_session, graph.episode_id, "suppress_derivatives")

    assert_that(_suppression_rows(db_session)).is_length(1)


def test_takedown_of_missing_subject_records_nothing(db_session: Session) -> None:
    """A subject that no longer exists has nothing to suppress, as before."""
    seed_catalog_graph(db_session)
    for subject_type in TakedownSubjectType:
        approve_takedown(
            db_session,
            subject_type=subject_type,
            subject_id=999_999,
            actions=["unpublish_mentions", "suppress_derivatives"],
        )

    assert_that(_suppression_rows(db_session)).is_empty()
    assert_that(db_session.query(Mention).count()).is_equal_to(1)


def test_failed_execution_writes_no_suppression(
    client: TestClient,
    db_session: Session,
) -> None:
    """An execution that fails part-way leaves no suppression and no deletion."""
    graph = seed_catalog_graph(db_session)
    _seed_episode_derivatives(db_session, graph.episode_id)
    submitted = client.post(
        "/api/v2/takedowns",
        json={
            "subject_type": "episode",
            "subject_id": graph.episode_id,
            "requester_type": "rights_holder",
            "requester_name": "Rights Holder",
            "requester_email": "rights@example.com",
            "basis": "This content infringes the rights we hold.",
            "requested_actions": [
                "unpublish_mentions",
                "suppress_derivatives",
                "register_source_opt_out",
            ],
        },
    )
    _enable_ops(client)

    decided = client.post(
        f"/api/v2/ops/takedown-requests/{submitted.json()['id']}/decide",
        json={"status": "approved", "note": "Ownership verified."},
        headers=_OPS_HEADERS,
    )

    assert_that(decided.status_code).is_equal_to(409)
    assert_that(decided.json()["detail"]).contains("creator request")
    assert_that(_suppression_rows(db_session)).is_empty()
    assert_that(db_session.query(Mention).count()).is_equal_to(1)
    assert_that(db_session.query(SemanticChunk).count()).is_equal_to(1)


def test_mention_pair_suppression_survives_candidate_detach(
    client: TestClient,
    db_session: Session,
) -> None:
    """The pair stays suppressed after its Mention is deleted and candidate freed."""
    graph = seed_catalog_graph(db_session)
    candidate = MentionCandidate(
        episode_id=graph.episode_id,
        media_id=graph.media_id,
        media_type="book",
        raw_title="Dune",
        confidence=0.9,
        mention_id=graph.mention_id,
    )
    db_session.add(candidate)
    db_session.commit()
    take_down_mention(db_session, graph.mention_id)
    db_session.refresh(candidate)
    assert_that(candidate.mention_id).is_none()

    # Publishing the freed candidate again, as a later review approval would.
    db_session.add(
        Mention(
            episode_id=candidate.episode_id,
            media_id=candidate.media_id,
            context="a great book",
        ),
    )
    with pytest.raises(ContentSuppressedError):
        db_session.commit()
    db_session.rollback()

    assert_that(db_session.query(Mention).count()).is_zero()
    listed = client.get(f"/api/v2/media/{graph.media_id}/mentions")
    assert_that(listed.json()["items"]).is_empty()


_DESTRUCTIVE_STEPS = (
    "_suppress_raw_transcripts",
    "_suppress_derivatives",
    "_unpublish_mentions",
    "_drop_media_summaries",
)


@pytest.mark.parametrize("subject_type", list(TakedownSubjectType))
def test_suppressions_are_recorded_before_anything_is_deleted(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    subject_type: TakedownSubjectType,
) -> None:
    """Every destructive step runs after the case's suppression rows exist."""
    graph = seed_catalog_graph(db_session)
    _seed_episode_derivatives(db_session, graph.episode_id)
    subject_ids = {
        TakedownSubjectType.PODCAST: graph.podcast_id,
        TakedownSubjectType.EPISODE: graph.episode_id,
        TakedownSubjectType.MENTION: graph.mention_id,
    }
    observed: dict[str, int] = {}

    def spy(name: str) -> Callable[..., object]:
        step = getattr(takedown_requests, name)

        def recorded(**kwargs: object) -> object:
            db_session.flush()
            observed[name] = db_session.query(ContentSuppression).count()
            return step(**kwargs)

        return recorded

    for name in _DESTRUCTIVE_STEPS:
        monkeypatch.setattr(takedown_requests, name, spy(name))

    approve_takedown(
        db_session,
        subject_type=subject_type,
        subject_id=subject_ids[subject_type],
        actions=[
            "suppress_raw_transcript",
            "suppress_derivatives",
            "unpublish_mentions",
        ],
    )

    assert_that(sorted(observed)).is_equal_to(sorted(_DESTRUCTIVE_STEPS))
    assert_that(set(observed.values())).is_equal_to({2})
