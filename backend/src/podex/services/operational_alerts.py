"""Threshold-based alerts derived from operational health metrics."""

from dataclasses import dataclass
from typing import Literal

from podex.services.ops_metrics import OperationalMetricsData


@dataclass(frozen=True, slots=True)
class OperationalAlertThresholdsData:
    """Configuration values that turn metric pressure into alerts."""

    review_pending: int
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
