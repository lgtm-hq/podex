"""Admin-focused v2 API endpoints."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from podex.api.v2.schemas import AdminSettingsResponse, AdminSettingsUpdateRequest
from podex.config import get_settings
from podex.database import get_db
from podex.models import AuditAction
from podex.services.admin_settings import (
    UpdateAdminSettingsInputData,
    get_admin_settings_snapshot,
    update_admin_settings,
)
from podex.services.audit_log import record_audit_log

router = APIRouter(prefix="/admin", tags=["v2-admin"])


def _validate_admin_settings_update_request(
    *,
    payload: AdminSettingsUpdateRequest,
) -> None:
    """Validate partial admin settings updates before applying changes.

    Args:
        payload: Partial runtime configuration update payload.

    Raises:
        HTTPException: If a non-clearable field is explicitly set to ``null``.
    """
    invalid_null_fields = [
        field_name
        for field_name in ("debug", "rate_limit_per_minute", "meilisearch_enabled")
        if field_name in payload.model_fields_set
        and getattr(payload, field_name) is None
    ]
    if invalid_null_fields:
        field_list = ", ".join(invalid_null_fields)
        raise HTTPException(
            status_code=422,
            detail=f"Fields cannot be null: {field_list}",
        )


@router.get("/settings", response_model=AdminSettingsResponse)
def get_admin_settings_route() -> AdminSettingsResponse:
    """Get the current runtime configuration snapshot for admin views.

    Returns:
        Current runtime configuration snapshot.
    """
    settings = get_settings()
    snapshot = get_admin_settings_snapshot(settings=settings)
    return AdminSettingsResponse(**asdict(snapshot))


@router.patch("/settings", response_model=AdminSettingsResponse)
def update_admin_settings_route(
    payload: AdminSettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> AdminSettingsResponse:
    """Apply a partial runtime configuration update for admin views.

    Args:
        payload: Partial runtime configuration update payload.

    Returns:
        Updated runtime configuration snapshot.
    """
    _validate_admin_settings_update_request(payload=payload)

    settings = get_settings()
    updated = update_admin_settings(
        settings=settings,
        payload=UpdateAdminSettingsInputData(
            provided_fields=frozenset(payload.model_fields_set),
            debug=payload.debug,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            cors_origins=payload.cors_origins,
            youtube_channel_id=payload.youtube_channel_id,
            meilisearch_enabled=payload.meilisearch_enabled,
            meilisearch_url=payload.meilisearch_url,
        ),
    )
    record_audit_log(
        db=db,
        action=AuditAction.UPDATE_SETTINGS,
        resource_type="settings",
        resource_identifier="runtime",
        summary="Updated runtime settings",
        metadata_json={"updated_fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    return AdminSettingsResponse(**asdict(updated))
