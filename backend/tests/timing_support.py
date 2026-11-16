"""Bound the cost of code under test so that one stall cannot fail a test.

A single wall-clock measurement fails whenever the process is descheduled or
a garbage collection runs inside it, however fast the code is. These helpers
time several runs and keep the fastest: a stall lengthens one run, while a
regression (unbounded or quadratic work) slows every run. Each action run is
paired with a run of a baseline, ordinary work of the same kind, and the two
alternate so that both see the same conditions. The fastest action run is
compared with a multiple of the fastest baseline run, so the bound follows
the speed of the machine running the test.

Garbage collection is disabled process-wide while the runs are timed, and
re-enabled afterwards if it was enabled. That is why the helpers are not
meant for tests run in parallel threads of one process.
"""

import gc
import math
import time
from collections.abc import Callable

from assertpy import assert_that

Run = Callable[[], object]
"""One run of timed work; its result is ignored."""

REPETITIONS = 5
"""Timed runs of each kind per measurement; only the fastest counts."""

BASELINE_FACTOR = 20.0
"""Default number of baselines the measured work may take.

When this was set, the bounded work in the log-boundary and container probe
tests measured at most 4.1 baselines, both on an idle machine and with a
busy loop on every core. With a protection removed, the same tests measured
53 to 28,000 baselines.
"""


def fastest_runs(
    make_action: Callable[[], Run],
    make_baseline: Callable[[], Run],
    repetitions: int = REPETITIONS,
) -> tuple[float, float]:
    """Time ``action`` and ``baseline`` runs alternately; return the fastest.

    Each run is built by its factory outside the timed region, so it can get
    fresh input, for example a new string whose hash is not cached yet. One
    untimed run of each comes first, so one-time work such as imports or
    lazily built tables is not counted. Garbage collection is disabled
    process-wide while the runs are timed and re-enabled afterwards if it was
    enabled, even when a run raises.

    Args:
        make_action: Returns the next run of the work being bounded.
        make_baseline: Returns the next run of the ordinary work.
        repetitions: How many timed runs of each to make.

    Returns:
        tuple[float, float]: The fastest action run and the fastest baseline
            run, in seconds.
    """
    make_baseline()()
    make_action()()
    collecting = gc.isenabled()
    gc.disable()
    try:
        fastest_action = fastest_baseline = math.inf
        for _ in range(repetitions):
            fastest_baseline = min(fastest_baseline, _timed(make_baseline()))
            fastest_action = min(fastest_action, _timed(make_action()))
    finally:
        if collecting:
            gc.enable()
    return fastest_action, fastest_baseline


def _timed(run: Run) -> float:
    """Return the seconds one call of ``run`` takes."""
    started = time.perf_counter()
    run()
    return time.perf_counter() - started


def assert_fast(
    make_action: Callable[[], Run],
    make_baseline: Callable[[], Run],
    *,
    factor: float = BASELINE_FACTOR,
    limit: float | None = None,
    repetitions: int = REPETITIONS,
) -> None:
    """Assert that the work takes at most ``factor`` times the baseline.

    The fastest of ``repetitions`` action runs must take less than ``factor``
    times the fastest baseline run (see :func:`fastest_runs`) and, when
    ``limit`` is given, less than ``limit`` seconds. A failure message gives
    both measured times and the limits.

    Args:
        make_action: Returns the next run of the work being bounded.
        make_baseline: Returns the next run of the ordinary work.
        factor: How many baselines the fastest action run may take.
        limit: Seconds the fastest action run must stay under, if any.
        repetitions: How many timed runs of each to make.
    """
    elapsed, reference = fastest_runs(make_action, make_baseline, repetitions)
    limits = f"{factor:g} baselines" + ("" if limit is None else f", {limit:g}s")
    measured = (
        f"fastest of {repetitions} runs {elapsed:.6f}s, "
        f"baseline {reference:.6f}s ({elapsed / reference:.1f} times); "
        f"limits {limits}"
    )
    assert_that(elapsed).described_as(measured).is_less_than(factor * reference)
    if limit is not None:
        assert_that(elapsed).described_as(measured).is_less_than(limit)
