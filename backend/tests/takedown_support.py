"""Shared helpers for approving takedowns in suppression tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import Mock

from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from podex.api.v2 import account_deps
from podex.models import (
    AccountAlertEvent,
    AccountUser,
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
from podex.services.account_digests import send_pending_digest
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


def execute_takedown(
    db: Session,
    *,
    subject_type: TakedownSubjectType,
    subject_id: int,
    actions: list[str],
) -> TakedownExecutionResultData:
    """Approve and execute a takedown without committing.

    Returns:
        The execution result.
    """
    request = submit_takedown(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        actions=actions,
    )
    return execute_approved_takedown_request(
        db=db,
        request=request,
        artifact_store=None,
    )


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
    result = execute_takedown(
        db,
        subject_type=subject_type,
        subject_id=subject_id,
        actions=actions,
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


def take_down_named(
    db: Session,
    subject: str,
    subject_ids: dict[str, int],
    actions: list[str],
) -> None:
    """Approve and execute a takedown of the subject named ``subject``."""
    approve_takedown(
        db,
        subject_type=TakedownSubjectType(subject),
        subject_id=subject_ids[subject],
        actions=actions,
    )


def deliver_nothing(db: Session, user: AccountUser) -> AccountAlertEvent:
    """Run a digest that must send nothing; return the one held-back event."""
    sender = Mock()
    digest = send_pending_digest(db=db, user=user, sender=sender)
    db.commit()
    assert_that(digest).is_none()
    assert_that(sender.mock_calls).is_empty()
    held: AccountAlertEvent = db.query(AccountAlertEvent).one()
    assert_that(held.digest_id).is_none()
    return held


def summary_values(episode_id: int, key: str, text: str) -> dict[str, object]:
    """Return the columns of one ready episode summary."""
    return {
        "episode_id": episode_id,
        "summary_key": key,
        "summary_kind": "overview",
        "pipeline_version": "v1",
        "prompt_version": "p1",
        "source_text_hash": "d" * 64,
        "source_model": "model",
        "summary_text": text,
        "generated_at": datetime(2026, 7, 1, tzinfo=UTC),
    }


@contextmanager
def recorded_statements(engine: Engine) -> Iterator[list[str]]:
    """Record the text of every statement ``engine`` runs inside the block."""
    recorded: list[str] = []

    def record(*args: object) -> None:
        recorded.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", record)
