"""Operational health metrics for the private dashboard."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy.orm import Session

from podex.models import (
    AccountAlertEvent,
    AccountDigest,
    ReviewItem,
    ReviewItemStatus,
)


@dataclass(frozen=True, slots=True)
class ReviewThroughputData:
    """Review decision activity and outstanding pressure."""

    pending_items: int
    decisions_last_24h: int
    median_decision_minutes_last_24h: float | None


@dataclass(frozen=True, slots=True)
class AlertDeliveryData:
    """Generated and delivered account notification health."""

    generated_events_last_24h: int
    delivered_digests_last_24h: int
    delivered_events_last_24h: int
    pending_events: int


@dataclass(frozen=True, slots=True)
class OperationalMetricsData:
    """Combined stabilization metrics for operator visibility."""

    measured_at: datetime
    review: ReviewThroughputData
    alerts: AlertDeliveryData


def get_operational_metrics(
    *,
    db: Session,
    now: datetime | None = None,
) -> OperationalMetricsData:
    """Calculate dashboard health metrics from authoritative operational records."""
    measured_at = now or datetime.now(UTC)
    cutoff = measured_at - timedelta(hours=24)
    pending_statuses = [
        ReviewItemStatus.PENDING.value,
        ReviewItemStatus.IN_REVIEW.value,
    ]
    decided_items = (
        db.query(ReviewItem)
        .filter(ReviewItem.decided_at.isnot(None))
        .filter(ReviewItem.decided_at >= cutoff)
        .all()
    )
    decision_minutes = [
        (item.decided_at - item.created_at).total_seconds() / 60
        for item in decided_items
        if item.decided_at is not None
    ]
    delivered_digests = (
        db.query(AccountDigest)
        .filter(AccountDigest.delivered_at.isnot(None))
        .filter(AccountDigest.delivered_at >= cutoff)
        .all()
    )
    return OperationalMetricsData(
        measured_at=measured_at,
        review=ReviewThroughputData(
            pending_items=db.query(ReviewItem)
            .filter(ReviewItem.status.in_(pending_statuses))
            .count(),
            decisions_last_24h=len(decided_items),
            median_decision_minutes_last_24h=(
                round(median(decision_minutes), 2) if decision_minutes else None
            ),
        ),
        alerts=AlertDeliveryData(
            generated_events_last_24h=db.query(AccountAlertEvent)
            .filter(AccountAlertEvent.created_at >= cutoff)
            .count(),
            delivered_digests_last_24h=len(delivered_digests),
            delivered_events_last_24h=sum(
                digest.event_count for digest in delivered_digests
            ),
            pending_events=db.query(AccountAlertEvent)
            .filter(AccountAlertEvent.digest_id.is_(None))
            .count(),
        ),
    )
