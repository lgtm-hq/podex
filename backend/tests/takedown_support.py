"""Shared helpers for approving takedowns in suppression tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from podex.api.v2 import account_deps
from podex.models import (
    ContentSuppressionScope,
    DerivativeGenerationRun,
    DerivativeSummaryKind,
    Episode,
    EpisodeSummary,
    Mention,
    SemanticChunk,
    TakedownRequest,
    TakedownRequesterType,
    TakedownRequestStatus,
    TakedownSubjectType,
)
from podex.services.content_suppression import suppressed_episode_ids
from podex.services.derivative_summaries import upsert_episode_summary
from podex.services.takedown_requests import (
    TakedownDecisionInputData,
    TakedownExecutionResultData,
    TakedownRequestInputData,
    create_takedown_request,
    decide_takedown_request,
    execute_approved_takedown_request,
)
from tests.test_api_auth import CapturingDigestSender, _sign_in


def submit_takedown(
    db: Session,
    *,
    subject_type: TakedownSubjectType,
    subject_id: int,
    actions: list[str],
) -> TakedownRequest:
    """Persist and approve a rights-holder takedown without executing it.

    Returns:
        The approved, not yet executed, case.
    """
    request = create_takedown_request(
        db=db,
        payload=TakedownRequestInputData(
            subject_type=subject_type,
            subject_id=subject_id,
            requester_type=TakedownRequesterType.RIGHTS_HOLDER,
            requester_name="Rights Holder",
            requester_email="rights@example.com",
            basis="Synthetic basis for a suppression test.",
            requested_actions=actions,
        ),
    )
    decide_takedown_request(
        db=db,
        request_id=request.id,
        payload=TakedownDecisionInputData(
            status=TakedownRequestStatus.APPROVED,
            actor_name="operator",
            note="Verified.",
        ),
    )
    return request


def approve_takedown(
    db: Session,
    *,
    subject_type: TakedownSubjectType,
    subject_id: int,
    actions: list[str],
) -> TakedownExecutionResultData:
    """Approve and execute a takedown, then commit, as the ops route does.

    Returns:
        The execution result.
    """
    request = submit_takedown(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        actions=actions,
    )
    result = execute_approved_takedown_request(
        db=db,
        request=request,
        artifact_store=None,
    )
    db.commit()
    return result


def take_down_mention(db: Session, mention_id: int) -> None:
    """Approve and execute an ``unpublish_mentions`` takedown of one mention."""
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.MENTION,
        subject_id=mention_id,
        actions=["unpublish_mentions"],
    )


def take_down_new_mention(db: Session, *, episode_id: int, media_id: int) -> int:
    """Publish a mention, then take it down.

    Returns:
        The id the deleted mention had.
    """
    mention = Mention(episode_id=episode_id, media_id=media_id)
    db.add(mention)
    db.commit()
    mention_id: int = mention.id
    take_down_mention(db, mention_id)
    return mention_id


def assert_derivatives_taken_down(db: Session, episode_id: int) -> None:
    """The episode is suppressed for derivatives and has no derived rows."""
    db.expire_all()
    assert_that(
        suppressed_episode_ids(
            db=db,
            scope=ContentSuppressionScope.DERIVATIVES,
            episode_ids=[episode_id],
        ),
    ).is_equal_to({episode_id})
    assert_that(db.query(SemanticChunk).count()).is_zero()
    assert_that(db.query(DerivativeGenerationRun).count()).is_zero()


def take_down_episode(db: Session, episode_id: int, *actions: str) -> None:
    """Approve and execute an episode-level takedown with ``actions``."""
    approve_takedown(
        db,
        subject_type=TakedownSubjectType.EPISODE,
        subject_id=episode_id,
        actions=list(actions),
    )


def write_episode_summary(db: Session, episode: Episode, text: str) -> EpisodeSummary:
    """Upsert an episode summary directly, without authorising its text.

    Returns:
        The written summary.
    """
    return upsert_episode_summary(
        db=db,
        episode=episode,
        summary_kind=DerivativeSummaryKind.OVERVIEW,
        pipeline_version="v1",
        prompt_version="p1",
        source_model="model",
        source_text_hash="a" * 64,
        summary_text=text,
        generated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )


@dataclass(frozen=True, slots=True)
class SignedInReader:
    """A signed-in account with a media alert and a capturing digest sender."""

    headers: dict[str, str]
    sender: CapturingDigestSender
    rule_id: int


def sign_in_reader_with_alert(client: TestClient, *, media_id: int) -> SignedInReader:
    """Sign in, capture digests, save ``media_id`` and alert on new mentions.

    Returns:
        The request headers, the digest sender and the alert rule id.
    """
    headers = {"Cookie": f"podex_session={_sign_in(client)}"}
    sender = CapturingDigestSender()
    app = client.app
    if not isinstance(app, FastAPI):
        raise TypeError(type(app))
    app.dependency_overrides[account_deps.get_digest_sender] = lambda: sender
    saved = client.put(f"/api/v2/me/saves/{media_id}", headers=headers)
    assert_that(saved.status_code).is_equal_to(200)
    rule = client.post(
        "/api/v2/me/alerts",
        headers=headers,
        json={
            "target_type": "media",
            "target_id": media_id,
            "event_type": "new_mention",
        },
    )
    assert_that(rule.status_code).is_equal_to(200)
    return SignedInReader(
        headers=headers, sender=sender, rule_id=int(rule.json()["id"])
    )
