"""The timing helper: slow work fails, one stall does not, collection returns."""

import gc
import time
from collections.abc import Iterator
from functools import partial

import pytest
from assertpy import assert_that

from tests.timing_support import Run, assert_fast, fastest_runs


def _work(size: int) -> int:
    """Do a fixed amount of pure computation."""
    return sum(range(size))


def _ordinary() -> Run:
    """Return one run of the ordinary work."""
    return partial(_work, 1_000)


def test_slow_action_fails_with_its_measurements() -> None:
    """Work a thousand times the baseline fails, naming the runs it made."""
    with pytest.raises(AssertionError, match="fastest of 3 runs"):
        assert_fast(lambda: partial(_work, 1_000_000), _ordinary, repetitions=3)


def test_absolute_limit_applies() -> None:
    """Work within the relative bound still fails past the absolute limit."""
    with pytest.raises(AssertionError, match="limits 20 baselines, 1e-09s"):
        assert_fast(_ordinary, _ordinary, limit=1e-9)


def test_one_stalled_run_passes() -> None:
    """A run delayed by a stall does not count against the work."""
    runs: Iterator[Run] = iter(
        [
            _ordinary(),
            _ordinary(),
            lambda: time.sleep(0.05),
            _ordinary(),
            _ordinary(),
            _ordinary(),
        ],
    )

    assert_fast(lambda: next(runs), _ordinary)


@pytest.mark.parametrize("collecting", [True, False])
def test_collection_is_paused_and_restored(collecting: bool) -> None:
    """Runs are timed with collection off; its state comes back after a failure."""
    states: list[bool] = []

    def failing() -> None:
        states.append(gc.isenabled())
        if len(states) > 1:
            raise RuntimeError("failed run")

    was_enabled = gc.isenabled()
    if not collecting:
        gc.disable()
    try:
        with pytest.raises(RuntimeError, match="failed run"):
            fastest_runs(lambda: failing, _ordinary)
        assert_that(gc.isenabled()).is_equal_to(collecting)
    finally:
        if was_enabled:
            gc.enable()
    assert_that(states).is_equal_to([collecting, False])
