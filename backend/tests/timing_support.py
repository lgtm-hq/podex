"""Bound the cost of code under test so that one stall cannot fail a test.

A single wall-clock measurement fails whenever the process is descheduled or
a garbage collection runs inside it, however fast the code is. These helpers
time several runs with the collector paused and keep the fastest: a stall
lengthens one run, while a regression (unbounded or quadratic work) slows
every run. The fastest run is compared with a fixed limit and with a
baseline of ordinary work timed the same way in the same process, so the
relative bound follows the speed of the machine running the test.
"""

import gc
import math
import time
from collections.abc import Callable

from assertpy import assert_that

REPETITIONS = 5
"""Timed runs per measurement; only the fastest counts."""

BASELINE_FACTOR = 200.0
"""Default number of baselines the measured work may take.

Bounded work measured at up to 35 times its baseline when this was set;
the unbounded and quadratic cases it guards against cost thousands of times
more.
"""


def fastest_run(action: Callable[[], object], repetitions: int = REPETITIONS) -> float:
    """Return the seconds taken by the fastest of several runs of ``action``.

    ``action`` first runs once untimed, so one-time work such as imports or
    lazily built tables is not counted. Garbage collection is paused while
    the runs are timed and restored afterwards, even when ``action`` raises.

    Args:
        action: The work to time; its result is ignored.
        repetitions: How many timed runs to make.

    Returns:
        float: The shortest run, in seconds.
    """
    action()
    collecting = gc.isenabled()
    gc.disable()
    try:
        fastest = math.inf
        for _ in range(repetitions):
            started = time.perf_counter()
            action()
            fastest = min(fastest, time.perf_counter() - started)
    finally:
        if collecting:
            gc.enable()
    return fastest


def assert_fast(
    action: Callable[[], object],
    *,
    baseline: Callable[[], object],
    limit: float,
    factor: float = BASELINE_FACTOR,
) -> None:
    """Assert that ``action`` stays within a fixed and a relative bound.

    The fastest of several runs of ``action`` must take less than ``limit``
    seconds and less than ``factor`` times the fastest run of ``baseline``,
    which is timed first in the same way. A failure message gives both
    measured times and both limits.

    Args:
        action: The work whose cost is bounded.
        baseline: Ordinary work of the same kind.
        limit: Seconds the fastest run must stay under.
        factor: How many baselines the fastest run may take.
    """
    reference = fastest_run(baseline)
    elapsed = fastest_run(action)
    measured = (
        f"fastest of {REPETITIONS} runs {elapsed:.6f}s, "
        f"baseline {reference:.6f}s, limits {limit:g}s and {factor:g} baselines"
    )
    assert_that(elapsed).described_as(measured).is_less_than(limit)
    assert_that(elapsed).described_as(measured).is_less_than(factor * reference)
