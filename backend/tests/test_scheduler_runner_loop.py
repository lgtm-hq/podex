"""Tests for the scheduler runner's long-running loop and stop handling.

The loop runs in-process against the test database. Signal installation,
``time.sleep`` and the session factory are replaced with recorders, so a test
drives the loop by invoking the recorded stop handler at a chosen point.

Every loop is bounded. The session factory and ``sleep`` both run outside the
loop's ``try`` block, so when a stop regression keeps the loop running, the
bound raises ``AssertionError`` out of ``run_forever`` and the test fails
instead of hanging. A failure inside a tick (for example a stop handler that
was never installed) is swallowed by the loop, and the bound catches it a few
ticks later.
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
from podex.config import AuthSettings, Settings
from podex.metrics import build_metrics
from podex.models import PipelineSchedule, Podcast, ScheduledWorkItemModel
from podex.scheduler_runner import SchedulerRunner
from podex.services.notification_delivery import SmtpDigestSender

_LOGGER = "podex.scheduler_runner"
_MARKER_SLUG = "failed-tick-marker"

Handler = Callable[[int, FrameType | None], None]


@dataclass
class _Sessions:
    """The sessions the loop opened, bounded, with each one's ``close`` recorded.

    Attributes:
        factory: Real session factory bound to the test database.
        patch: Monkeypatch used to record each session's ``close``.
        limit: Sessions (ticks) the loop may open before the test fails.
    """

    factory: Callable[[], Session]
    patch: pytest.MonkeyPatch
    limit: int = 5
    opened: list[Session] = field(default_factory=list)
    closed: list[Session] = field(default_factory=list)

    def open(self) -> Session:
        """Open the next tick's session, failing once the tick bound is spent."""
        if len(self.opened) >= self.limit:
            raise AssertionError(f"loop did not stop after {self.limit} ticks")
        session = self.factory()
        real_close = session.close

        def close() -> None:
            self.closed.append(session)
            real_close()

        self.patch.setattr(session, "close", close)
        self.opened.append(session)
        return session


@dataclass
class _Process:
    """What the loop did to the process: signals, sleeps and sessions.

    Attributes:
        sessions: Bounded, recorded session factory installed as SessionLocal.
        max_sleeps: Sleep slices the loop may take before the test fails.
    """

    sessions: _Sessions
    max_sleeps: int = 20
    handlers: dict[int, Any] = field(default_factory=dict)
    sleeps: list[float] = field(default_factory=list)
    on_sleep: Callable[[int], None] | None = None

    def install(self, signum: int, handler: Any) -> None:
        """Record a signal handler instead of installing it."""
        self.handlers[signum] = handler

    def sleep(self, seconds: float) -> None:
        """Record a sleep slice and run the per-slice hook."""
        if len(self.sleeps) >= self.max_sleeps:
            raise AssertionError(
                f"loop did not stop after {self.max_sleeps} sleep slices",
            )
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))

    def deliver(self, signum: int) -> None:
        """Invoke the handler the loop registered for ``signum``."""
        if signum not in self.handlers:
            raise AssertionError(f"no handler installed for signal {signum}")
        handler: Handler = self.handlers[signum]
        handler(signum, None)


@pytest.fixture(name="process")
def process_fixture(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> _Process:
    """Point the runner at the test database and record signals and sleeps."""
    recorded = _Process(
        sessions=_Sessions(
            factory=sessionmaker(bind=db_session.get_bind(), autoflush=False),
            patch=monkeypatch,
        ),
    )
    monkeypatch.setattr(scheduler_runner.signal, "signal", recorded.install)
    monkeypatch.setattr(scheduler_runner.time, "sleep", recorded.sleep)
    monkeypatch.setattr(scheduler_runner, "SessionLocal", recorded.sessions.open)
    return recorded


def _settings(tick_seconds: int, *, auth: AuthSettings | None = None) -> Settings:
    """Return runner settings with a short tick and hourly notification work."""
    return Settings(
        scheduler_tick_seconds=tick_seconds,
        scheduler_digest_interval_minutes=60,
        scheduler_retention_interval_minutes=60,
        auth=auth or AuthSettings(),
    )


def _committed(db: Session) -> tuple[int, int, int]:
    """Return committed schedule, work item and failed-tick marker counts.

    The test's own session ends its transaction first, so it reads only what
    the runner's sessions committed.
    """
    db.rollback()
    schedules = db.scalar(select(func.count()).select_from(PipelineSchedule))
    items = db.scalar(select(func.count()).select_from(ScheduledWorkItemModel))
    markers = db.scalar(
        select(func.count()).select_from(Podcast).where(Podcast.slug == _MARKER_SLUG),
    )
    return schedules or 0, items or 0, markers or 0


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


def _runner(tick_seconds: int, *, auth: AuthSettings | None = None) -> SchedulerRunner:
    """Return a runner with its own metrics registry."""
    return SchedulerRunner(
        settings=_settings(tick_seconds, auth=auth),
        metrics=build_metrics(),
    )


def test_run_forever_installs_stop_handlers_for_sigterm_and_sigint(
    process: _Process,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both termination signals end the loop; a stop before the first tick runs none."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    process.sessions.limit = 0
    runner = _runner(tick_seconds=3)
    runner.request_stop(signal.SIGINT, None)

    runner.run_forever()

    assert_that(process.handlers).is_equal_to(
        {
            signal.SIGTERM: runner.request_stop,
            signal.SIGINT: runner.request_stop,
        },
    )
    assert_that(process.sessions.opened).is_empty()
    assert_that(_tick_samples(runner, "success")).is_zero()
    assert_that(_tick_samples(runner, "error")).is_zero()
    assert_that(process.sleeps).is_empty()
    assert_that(_messages(caplog)).is_equal_to(
        ["scheduler_runner_started tick_seconds=3", "scheduler_runner_stopped"],
    )


def test_failing_tick_leaves_no_writes_and_the_loop_continues(
    process: _Process,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A tick that raises after writing leaves nothing behind; the next tick runs."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = _runner(tick_seconds=2)
    real_digest_work = scheduler_runner.run_due_digest_work
    markers_seen_by_next_tick: list[int] = []

    def digest_work(**kwargs: Any) -> Any:
        """Write and fail on the first tick; on the second, look and stop."""
        db: Session = kwargs["db"]
        if not markers_seen_by_next_tick and len(process.sessions.opened) == 1:
            db.add(Podcast(name="Failed tick marker", slug=_MARKER_SLUG))
            db.flush()
            raise RuntimeError("digest delivery unavailable")
        markers_seen_by_next_tick.append(
            db.scalar(
                select(func.count())
                .select_from(Podcast)
                .where(Podcast.slug == _MARKER_SLUG),
            )
            or 0,
        )
        process.deliver(signal.SIGTERM)
        return real_digest_work(**kwargs)

    monkeypatch.setattr(scheduler_runner, "run_due_digest_work", digest_work)
    between_ticks: list[tuple[int, int, int]] = []
    process.on_sleep = lambda _slice: between_ticks.append(_committed(db_session))

    runner.run_forever()

    assert_that(between_ticks).is_equal_to([(0, 0, 0), (0, 0, 0)])
    assert_that(markers_seen_by_next_tick).is_equal_to([0])
    schedules, items, markers = _committed(db_session)
    assert_that(schedules).is_greater_than_or_equal_to(2)
    assert_that(items).is_greater_than_or_equal_to(2)
    assert_that(markers).is_zero()
    assert_that(process.sessions.opened).is_length(2)
    assert_that(process.sessions.closed).is_equal_to(process.sessions.opened)
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
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop requested mid-tick lets the tick commit and skips the sleep."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = _runner(tick_seconds=60)
    real_retention_work = scheduler_runner.run_due_retention_work

    def retention_work(**kwargs: Any) -> Any:
        """Receive SIGINT while the tick is still running."""
        process.deliver(signal.SIGINT)
        return real_retention_work(**kwargs)

    monkeypatch.setattr(scheduler_runner, "run_due_retention_work", retention_work)

    runner.run_forever()

    schedules, items, _markers = _committed(db_session)
    assert_that(schedules).is_greater_than_or_equal_to(2)
    assert_that(items).is_greater_than_or_equal_to(2)
    assert_that(process.sessions.opened).is_length(1)
    assert_that(process.sessions.closed).is_equal_to(process.sessions.opened)
    assert_that(_tick_samples(runner, "success")).is_equal_to(1.0)
    assert_that(process.sleeps).is_empty()
    messages = _messages(caplog)
    assert_that(messages).is_length(3)
    assert_that(messages[1]).starts_with("scheduler_tick planned=")
    assert_that(messages[2]).is_equal_to("scheduler_runner_stopped")


def test_each_tick_receives_the_configured_digest_sender(
    process: _Process,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With SMTP configured, the digest step gets an SMTP sender for those settings."""
    runner = _runner(
        tick_seconds=60,
        auth=AuthSettings(
            smtp_host="smtp.example.com",
            smtp_port=2525,
            smtp_from_email="digests@example.com",
        ),
    )
    senders: list[object] = []
    real_digest_work = scheduler_runner.run_due_digest_work

    def digest_work(**kwargs: Any) -> Any:
        """Record the sender handed to the digest step, then stop."""
        senders.append(kwargs["sender"])
        process.deliver(signal.SIGTERM)
        return real_digest_work(**kwargs)

    monkeypatch.setattr(scheduler_runner, "run_due_digest_work", digest_work)

    runner.run_forever()

    assert_that(senders).is_length(1)
    sender = senders[0]
    if not isinstance(sender, SmtpDigestSender):
        raise AssertionError(f"expected an SMTP digest sender, got {sender!r}")
    assert_that((sender.host, sender.port, sender.from_email)).is_equal_to(
        ("smtp.example.com", 2525, "digests@example.com"),
    )


def test_sleep_checks_the_stop_flag_each_second(
    process: _Process,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop that arrives while sleeping ends the wait at the next one-second slice."""
    caplog.set_level(logging.INFO, logger=_LOGGER)
    runner = _runner(tick_seconds=5)

    def stop_on_second_slice(slice_number: int) -> None:
        """Deliver SIGTERM during the second one-second slice."""
        if slice_number == 2:
            process.deliver(signal.SIGTERM)

    process.on_sleep = stop_on_second_slice

    runner.run_forever()

    assert_that(process.sleeps).is_equal_to([1, 1])
    assert_that(process.sessions.opened).is_length(1)
    assert_that(process.sessions.closed).is_equal_to(process.sessions.opened)
    assert_that(_tick_samples(runner, "success")).is_equal_to(1.0)
    assert_that(_messages(caplog)[-1]).is_equal_to("scheduler_runner_stopped")


def test_full_wait_sleeps_once_per_configured_second(
    process: _Process,
) -> None:
    """Without a stop the loop sleeps the whole tick interval in one-second slices."""
    runner = _runner(tick_seconds=3)

    def stop_after_full_wait(slice_number: int) -> None:
        """Let one whole interval pass, then stop during the next tick's wait."""
        if slice_number == 4:
            process.deliver(signal.SIGTERM)

    process.on_sleep = stop_after_full_wait

    runner.run_forever()

    assert_that(process.sleeps).is_equal_to([1, 1, 1, 1])
    assert_that(process.sessions.opened).is_length(2)
    assert_that(process.sessions.closed).is_equal_to(process.sessions.opened)
    assert_that(_tick_samples(runner, "success")).is_equal_to(2.0)
