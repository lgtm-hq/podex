"""Threshold-based alerts derived from operational health metrics."""

from dataclasses import dataclass
from typing import Literal

from podex.services.ops_metrics import OperationalMetricsData


@dataclass(frozen=True, slots=True)
class OperationalAlertThresholdsData:
    """Configuration values that turn metric pressure into alerts."""

    review_pending: int
    projection_pending: int
    projection_oldest_pending_minutes: int
    alert_delivery_pending: int


@dataclass(frozen=True, slots=True)
class OperationalAlertData:
    """One actionable operator-facing health alert."""

    key: str
    severity: Literal["warning", "critical"]
    title: str
    message: str
    current_value: int
    threshold: int
    playbook_slug: str


def evaluate_operational_alerts(
    *,
    metrics: OperationalMetricsData,
    thresholds: OperationalAlertThresholdsData,
) -> list[OperationalAlertData]:
    """Evaluate operational metrics against configured intervention thresholds."""
    alerts: list[OperationalAlertData] = []
    if metrics.review.pending_items >= thresholds.review_pending:
        alerts.append(
            OperationalAlertData(
                key="review_backlog",
                severity="warning",
                title="Review queue backlog is elevated",
                message="Pending review work is above the operator response threshold.",
                current_value=metrics.review.pending_items,
                threshold=thresholds.review_pending,
                playbook_slug="review-backlog",
            )
        )
    if metrics.projection.pending_repairs >= thresholds.projection_pending:
        alerts.append(
            OperationalAlertData(
                key="projection_backlog",
                severity="warning",
                title="Search projection backlog is elevated",
                message="Pending repairs may leave discovery records stale.",
                current_value=metrics.projection.pending_repairs,
                threshold=thresholds.projection_pending,
                playbook_slug="projection-lag",
            )
        )
    oldest_age_seconds = metrics.projection.oldest_pending_age_seconds
    oldest_minutes = (oldest_age_seconds or 0) // 60
    if (
        oldest_age_seconds is not None
        and oldest_minutes >= thresholds.projection_oldest_pending_minutes
    ):
        alerts.append(
            OperationalAlertData(
                key="projection_age",
                severity="critical",
                title="Search projection repair is stale",
                message="At least one projection repair has waited beyond its SLA.",
                current_value=oldest_minutes,
                threshold=thresholds.projection_oldest_pending_minutes,
                playbook_slug="projection-lag",
            )
        )
    if metrics.projection.failed_repairs > 0:
        alerts.append(
            OperationalAlertData(
                key="projection_failures",
                severity="critical",
                title="Search projection repair failed",
                message="Failed repair work requires investigation and replay.",
                current_value=metrics.projection.failed_repairs,
                threshold=0,
                playbook_slug="projection-lag",
            )
        )
    if metrics.alerts.pending_events >= thresholds.alert_delivery_pending:
        alerts.append(
            OperationalAlertData(
                key="delivery_backlog",
                severity="warning",
                title="Notification delivery backlog is elevated",
                message="Generated account alerts are awaiting digest delivery.",
                current_value=metrics.alerts.pending_events,
                threshold=thresholds.alert_delivery_pending,
                playbook_slug="notification-delivery",
            )
        )
    return alerts
