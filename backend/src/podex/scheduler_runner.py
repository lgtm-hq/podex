"""Scheduler runner deployable.

Runs the recurring-work loop as its own process (``python -m
podex.scheduler_runner``): each tick reconciles schedules, plans due
interval work, and executes pending discovery, digest delivery, and
retention sweeps. The API deployable stays request-only; this process owns
all background cadence.
"""

import signal
import time
from dataclasses import dataclass
from types import FrameType

from sqlalchemy.orm import Session

from podex.config import Settings, get_settings
from podex.database import SessionLocal
from podex.logging_config import get_logger
from podex.metrics import AppMetrics, build_metrics, observe_scheduler_tick
from podex.observability import init_sentry
from podex.services.notification_delivery import DigestSender, build_digest_sender
from podex.services.recurring_discovery import (
    reconcile_recurring_discovery_schedules,
    run_due_episode_discovery_work,
)
from podex.services.recurring_notifications import (
    reconcile_notification_schedules,
    run_due_digest_work,
    run_due_retention_work,
)
from podex.services.scheduled_work import plan_due_scheduled_work

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TickSummaryData:
    """Work performed during a single scheduler tick."""

    planned: int
    discovery_runs: int
    digest_runs: int
    retention_runs: int


def run_tick(
    *,
    db: Session,
    settings: Settings,
    digest_sender: DigestSender | None,
) -> TickSummaryData:
    """Run one scheduler tick: reconcile, plan, and execute due work.

    Args:
        db: Database session.
        settings: Runtime settings controlling cadences.
        digest_sender: Configured digest delivery boundary, if any.

    Returns:
        Summary of the work performed this tick.
    """
    reconcile_recurring_discovery_schedules(db=db)
    reconcile_notification_schedules(
        db=db,
        digest_interval_minutes=settings.scheduler_digest_interval_minutes,
        retention_interval_minutes=settings.scheduler_retention_interval_minutes,
    )
    planned = plan_due_scheduled_work(db=db)
    discovery = run_due_episode_discovery_work(db=db)
    digests = run_due_digest_work(db=db, sender=digest_sender)
    retention = run_due_retention_work(db=db)
    db.commit()
    return TickSummaryData(
        planned=len(planned),
        discovery_runs=len(discovery),
        digest_runs=len(digests),
        retention_runs=len(retention),
    )


def run_instrumented_tick(
    *,
    db: Session,
    settings: Settings,
    digest_sender: DigestSender | None,
    metrics: AppMetrics,
) -> TickSummaryData:
    """Run one tick, recording its duration and outcome as metrics.

    Failures are recorded with outcome ``error`` and re-raised unchanged so
    the caller's rollback/log handling still applies.

    Args:
        db: Database session.
        settings: Runtime settings controlling cadences.
        digest_sender: Configured digest delivery boundary, if any.
        metrics: Collectors the tick sample is recorded on.

    Returns:
        Summary of the work performed this tick.
    """
    start = time.perf_counter()
    outcome = "error"
    try:
        summary = run_tick(db=db, settings=settings, digest_sender=digest_sender)
        outcome = "success"
        return summary
    finally:
        observe_scheduler_tick(
            metrics,
            duration_seconds=time.perf_counter() - start,
            outcome=outcome,
        )


class SchedulerRunner:
    """Long-running loop that executes scheduler ticks until stopped."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        metrics: AppMetrics | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # The runner is its own process, so it owns its own registry; the
        # samples are process-local (see podex.metrics module docstring).
        self.metrics = metrics or build_metrics()
        self._stopping = False

    def request_stop(self, signum: int, frame: FrameType | None) -> None:
        """Signal handler that ends the loop after the current tick."""
        del signum, frame
        self._stopping = True

    def run_forever(self) -> None:
        """Run ticks at the configured cadence until stop is requested."""
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)
        logger.info(
            "scheduler_runner_started tick_seconds=%s",
            self.settings.scheduler_tick_seconds,
        )
        while not self._stopping:
            db = SessionLocal()
            try:
                summary = run_instrumented_tick(
                    db=db,
                    settings=self.settings,
                    digest_sender=build_digest_sender(settings=self.settings),
                    metrics=self.metrics,
                )
                logger.info(
                    "scheduler_tick planned=%s discovery=%s digests=%s retention=%s",
                    summary.planned,
                    summary.discovery_runs,
                    summary.digest_runs,
                    summary.retention_runs,
                )
            except Exception:
                db.rollback()
                logger.exception("scheduler_tick_failed")
            finally:
                db.close()
            self._sleep_until_next_tick()
        logger.info("scheduler_runner_stopped")

    def _sleep_until_next_tick(self) -> None:
        """Sleep in one-second slices so stop requests stay responsive."""
        for _ in range(self.settings.scheduler_tick_seconds):
            if self._stopping:
                return
            time.sleep(1)


def main() -> None:
    """Entry point for the scheduler deployable."""
    # The runner is its own process, so it initializes Sentry independently
    # of the API deployable.
    init_sentry(get_settings())
    SchedulerRunner().run_forever()


if __name__ == "__main__":  # pragma: no cover - process entry point
    main()
