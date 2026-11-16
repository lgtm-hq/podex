"""Shared helpers for approving takedowns in suppression tests."""

from assertpy import assert_that
from sqlalchemy.orm import Session

from podex.models import (
    ContentSuppressionScope,
    DerivativeGenerationRun,
    Mention,
    SemanticChunk,
    TakedownRequest,
    TakedownRequesterType,
    TakedownRequestStatus,
    TakedownSubjectType,
)
from podex.services.content_suppression import suppressed_episode_ids
from podex.services.takedown_requests import (
    TakedownDecisionInputData,
    TakedownExecutionResultData,
    TakedownRequestInputData,
    create_takedown_request,
    decide_takedown_request,
    execute_approved_takedown_request,
)


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
