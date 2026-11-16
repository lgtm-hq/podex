"""Tests for the scheduler runner's long-running loop and stop handling.

The loop runs in-process against the test database. Signal installation and
``time.sleep`` are replaced with recorders, so a test drives the loop by
invoking the recorded stop handler at a chosen point.
"""

import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

import pytest
from assertpy import assert_that
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from podex import scheduler_runner
from podex.config import Settings
from podex.metrics import build_metrics
from podex.models import PipelineSchedule, ScheduledWorkItemModel
from podex.scheduler_runner import SchedulerRunner

_LOGGER = "podex.scheduler_runner"

Handler = Callable[[int, FrameType | None], None]


@dataclass
class _Process:
    """What the loop did to the process: signal handlers and sleeps."""

    handlers: dict[int, Any] = field(default_factory=dict)
    sleeps: list[float] = field(default_factory=list)
    on_sleep: Callable[[int], None] | None = None

    def install(self, signum: int, handler: Any) -> None:
        """Record a signal handler instead of installing it."""
        self.handlers[signum] = handler

    def sleep(self, seconds: float) -> None:
        """Record a sleep slice and run the per-slice hook."""
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))

    def deliver(self, signum: int) -> None:
        """Invoke the handler the loop registered for ``signum``."""
        handler: Handler = self.handlers[signum]
        handler(signum, None)


@pytest.fixture(name="process")
def process_fixture(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> _Process:
    """Point the runner at the test database and record signals and sleeps."""
    recorded = _Process()
    monkeypatch.setattr(scheduler_runner.signal, "signal", recorded.install)
    monkeypatch.setattr(scheduler_runner.time, "sleep", recorded.sleep)
    monkeypatch.setattr(
        scheduler_runner,
        "SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autoflush=False),
    )
    return recorded


def _settings(tick_seconds: int) -> Settings:
    """Return runner settings with a short tick and hourly notification work."""
    return Settings(
        scheduler_tick_seconds=tick_seconds,
        scheduler_digest_interval_minutes=60,
        scheduler_retention_interval_minutes=60,
    )


def _row_counts(db: Session) -> tuple[int, int]:
    """Return committed schedule and work item counts."""
    db.rollback()
    schedules = db.scalar(select(func.count()).select_from(PipelineSchedule))
    items = db.scalar(select(func.count()).select_from(ScheduledWorkItemModel))
    return schedules or 0, items or 0


def _tick_samples(runner: SchedulerRunner, outcome: str) -> float:
    """Return how many ticks the runner recorded with ``outcome``."""
    value = runner.metrics.registry.get_sample_value(
        "podex_scheduler_ticks_total",
        {"outcome": outcome},
    )
    return value or 0.0


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the runner's log lines in order."""
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


def test_run_forever_installs_stop_handlers_for_sigterm_and_sigint(
    process: _Process,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both termination signals end the loop; a stop before the first tick runs none."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = SchedulerRunner(
        settings=_settings(tick_seconds=3), metrics=build_metrics()
    )
    runner.request_stop(signal.SIGINT, None)

    runner.run_forever()

    assert_that(process.handlers).is_equal_to(
        {
            signal.SIGTERM: runner.request_stop,
            signal.SIGINT: runner.request_stop,
        },
    )
    assert_that(_tick_samples(runner, "success")).is_zero()
    assert_that(_tick_samples(runner, "error")).is_zero()
    assert_that(process.sleeps).is_empty()
    assert_that(_messages(caplog)).is_equal_to(
        ["scheduler_runner_started tick_seconds=3", "scheduler_runner_stopped"],
    )


def test_failing_tick_is_rolled_back_and_the_loop_continues(
    process: _Process,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tick that raises discards its writes, is logged, and the next tick runs."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = SchedulerRunner(
        settings=_settings(tick_seconds=2), metrics=build_metrics()
    )
    real_digest_work = scheduler_runner.run_due_digest_work
    digest_calls: list[int] = []

    def digest_work(**kwargs: Any) -> Any:
        """Fail the first tick after it has written; stop during the second."""
        digest_calls.append(len(digest_calls) + 1)
        if len(digest_calls) == 1:
            raise RuntimeError("digest delivery unavailable")
        process.deliver(signal.SIGTERM)
        return real_digest_work(**kwargs)

    monkeypatch.setattr(scheduler_runner, "run_due_digest_work", digest_work)
    between_ticks: list[tuple[int, int]] = []
    process.on_sleep = lambda _slice: between_ticks.append(_row_counts(db_session))

    runner.run_forever()

    assert_that(digest_calls).is_equal_to([1, 2])
    assert_that(between_ticks).is_equal_to([(0, 0), (0, 0)])
    schedules, items = _row_counts(db_session)
    assert_that(schedules).is_greater_than_or_equal_to(2)
    assert_that(items).is_greater_than_or_equal_to(2)
    assert_that(_tick_samples(runner, "error")).is_equal_to(1.0)
    assert_that(_tick_samples(runner, "success")).is_equal_to(1.0)
    assert_that(process.sleeps).is_equal_to([1, 1])
    messages = _messages(caplog)
    assert_that(messages[0]).is_equal_to("scheduler_runner_started tick_seconds=2")
    assert_that(messages[1]).is_equal_to("scheduler_tick_failed")
    assert_that(messages[2]).matches(
        r"^scheduler_tick planned=\d+ discovery=0 digests=0 retention=1$",
    )
    assert_that(messages[3:]).is_equal_to(["scheduler_runner_stopped"])
    failure = next(
        r for r in caplog.records if r.getMessage() == "scheduler_tick_failed"
    )
    assert_that(failure.levelno).is_equal_to(logging.ERROR)
    assert_that(failure.exc_info).is_not_none()
    assert_that(failure.exc_info[1] if failure.exc_info else None).is_instance_of(
        RuntimeError,
    )


def test_stop_during_a_tick_ends_the_loop_after_that_tick(
    process: _Process,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop requested mid-tick lets the tick commit and skips the sleep."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = SchedulerRunner(
        settings=_settings(tick_seconds=60), metrics=build_metrics()
    )
    real_retention_work = scheduler_runner.run_due_retention_work

    def retention_work(**kwargs: Any) -> Any:
        """Receive SIGINT while the tick is still running."""
        process.deliver(signal.SIGINT)
        return real_retention_work(**kwargs)

    monkeypatch.setattr(scheduler_runner, "run_due_retention_work", retention_work)

    runner.run_forever()

    assert_that(_tick_samples(runner, "success")).is_equal_to(1.0)
    assert_that(process.sleeps).is_empty()
    messages = _messages(caplog)
    assert_that(messages).is_length(3)
    assert_that(messages[1]).starts_with("scheduler_tick planned=")
    assert_that(messages[2]).is_equal_to("scheduler_runner_stopped")


def test_sleep_checks_the_stop_flag_each_second(
    process: _Process,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop that arrives while sleeping ends the wait at the next one-second slice."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = SchedulerRunner(
        settings=_settings(tick_seconds=5), metrics=build_metrics()
    )

    def stop_on_second_slice(slice_number: int) -> None:
        """Deliver SIGTERM during the second one-second slice."""
        if slice_number == 2:
            process.deliver(signal.SIGTERM)

    process.on_sleep = stop_on_second_slice

    runner.run_forever()

    assert_that(process.sleeps).is_equal_to([1, 1])
    assert_that(_tick_samples(runner, "success")).is_equal_to(1.0)
    assert_that(_messages(caplog)[-1]).is_equal_to("scheduler_runner_stopped")


def test_full_wait_sleeps_once_per_configured_second(
    process: _Process,
) -> None:
    """Without a stop the loop sleeps the whole tick interval in one-second slices."""
    runner = SchedulerRunner(
        settings=_settings(tick_seconds=3), metrics=build_metrics()
    )

    def stop_after_full_wait(slice_number: int) -> None:
        """Let one whole interval pass, then stop during the next tick's wait."""
        if slice_number == 4:
            process.deliver(signal.SIGTERM)

    process.on_sleep = stop_after_full_wait

    runner.run_forever()

    assert_that(process.sleeps).is_equal_to([1, 1, 1, 1])
    assert_that(_tick_samples(runner, "success")).is_equal_to(2.0)
