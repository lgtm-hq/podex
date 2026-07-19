"""Shared audit log services for ops and admin surfaces."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from podex.models import AuditAction, AuditLog


@dataclass(frozen=True, slots=True)
class AuditLogEntryData:
    """Audit log entry exposed to shared services and API layers."""

    id: int
    action: AuditAction
    resource_type: str
    resource_id: int | None
    resource_identifier: str | None
    actor_name: str | None
    summary: str
    metadata_json: dict[str, Any] | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuditLogListData:
    """Paginated audit log payload."""

    items: list[AuditLogEntryData]
    total: int
    page: int
    per_page: int


def _to_audit_log_entry_data(*, entry: AuditLog) -> AuditLogEntryData:
    """Convert a persisted audit log entry into shared data.

    Args:
        entry: Persisted audit log row.

    Returns:
        Shared audit log payload.
    """
    return AuditLogEntryData(
        id=entry.id,
        action=AuditAction(entry.action),
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        resource_identifier=entry.resource_identifier,
        actor_name=entry.actor_name,
        summary=entry.summary,
        metadata_json=entry.metadata_json,
        created_at=entry.created_at,
    )


def record_audit_log(
    *,
    db: Session,
    action: AuditAction,
    resource_type: str,
    summary: str,
    resource_id: int | None = None,
    resource_identifier: str | None = None,
    actor_name: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> AuditLogEntryData:
    """Persist an audit log entry for a privileged or content-affecting action.

    Args:
        db: Database session.
        action: Audit action enum.
        resource_type: Resource type that was affected.
        summary: Human-readable action summary.
        resource_id: Optional internal resource identifier.
        resource_identifier: Optional non-numeric resource identifier.
        actor_name: Optional actor name.
        metadata_json: Optional structured metadata.

    Returns:
        Persisted audit log entry data.
    """
    entry = AuditLog(
        action=action.value,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_identifier=resource_identifier,
        actor_name=actor_name,
        summary=summary,
        metadata_json=metadata_json,
    )
    db.add(entry)
    db.flush()
    return _to_audit_log_entry_data(entry=entry)


def list_audit_logs(
    *,
    db: Session,
    page: int,
    per_page: int,
    action: AuditAction | None = None,
    resource_type: str | None = None,
) -> AuditLogListData:
    """List audit log entries for ops and admin views.

    Args:
        db: Database session.
        page: Requested page number.
        per_page: Number of entries per page.
        action: Optional audit action filter.
        resource_type: Optional resource type filter.

    Returns:
        Paginated audit log payload ordered by recency.
    """
    query = db.query(AuditLog)

    if action is not None:
        query = query.filter(AuditLog.action == action.value)
    if resource_type is not None:
        query = query.filter(AuditLog.resource_type == resource_type)

    total = query.count()
    entries = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return AuditLogListData(
        items=[_to_audit_log_entry_data(entry=entry) for entry in entries],
        total=total,
        page=page,
        per_page=per_page,
    )
