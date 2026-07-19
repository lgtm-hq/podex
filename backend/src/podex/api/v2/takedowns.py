"""Public takedown intake endpoint.

Reachable without an account so rights holders and creators can submit a
case directly. The app-wide rate limiter covers this route; responses
disclose only the case id and status so submissions cannot be used to probe
catalog contents.
"""

from fastapi import APIRouter

from podex.api.deps import DbSession
from podex.models import (
    AuditAction,
    TakedownRequesterType,
    TakedownSubjectType,
)
from podex.schemas.ops import TakedownRequestCreate, TakedownRequestCreatedRead
from podex.services.audit_log import record_audit_log
from podex.services.takedown_requests import (
    TakedownRequestInputData,
    create_takedown_request,
)

router = APIRouter(tags=["takedowns"])


def submit_takedown_request(
    payload: TakedownRequestCreate,
    db: DbSession,
) -> TakedownRequestCreatedRead:
    """Accept a takedown submission for privileged review."""
    request = create_takedown_request(
        db=db,
        payload=TakedownRequestInputData(
            subject_type=TakedownSubjectType(payload.subject_type),
            subject_id=payload.subject_id,
            requester_type=TakedownRequesterType(payload.requester_type),
            requester_name=payload.requester_name,
            requester_email=payload.requester_email,
            basis=payload.basis,
            requested_actions=list(payload.requested_actions),
        ),
    )
    record_audit_log(
        db=db,
        action=AuditAction.SUBMIT_TAKEDOWN_REQUEST,
        resource_type="takedown_request",
        resource_id=request.id,
        summary=(f"Takedown submitted for {request.subject_type} {request.subject_id}"),
    )
    db.commit()
    return TakedownRequestCreatedRead(id=request.id, status=request.status)


router.add_api_route(
    "/takedowns",
    submit_takedown_request,
    methods=["POST"],
    response_model=TakedownRequestCreatedRead,
    status_code=202,
)
