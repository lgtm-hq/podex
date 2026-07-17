"""Unit tests for the sliding-window rate limiter."""

import pytest
from assertpy import assert_that

from podex.services.limiter import SlidingWindowRateLimiter


def test_allows_requests_under_the_limit() -> None:
    """Requests below the ceiling are permitted with decreasing remaining quota."""
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)

    first = limiter.check("client", now=0.0)
    second = limiter.check("client", now=1.0)

    assert_that(first.allowed).is_true()
    assert_that(first.remaining).is_equal_to(2)
    assert_that(second.allowed).is_true()
    assert_that(second.remaining).is_equal_to(1)


def test_blocks_requests_over_the_limit() -> None:
    """The request that exceeds the ceiling is denied with a retry hint."""
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)

    limiter.check("client", now=0.0)
    limiter.check("client", now=1.0)
    denied = limiter.check("client", now=2.0)

    assert_that(denied.allowed).is_false()
    assert_that(denied.remaining).is_equal_to(0)
    assert_that(denied.limit).is_equal_to(2)
    assert_that(denied.retry_after).is_equal_to(58.0)


def test_window_slides_to_free_capacity() -> None:
    """Hits older than the window are evicted, freeing capacity again."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=10.0)

    assert_that(limiter.check("client", now=0.0).allowed).is_true()
    assert_that(limiter.check("client", now=5.0).allowed).is_false()
    assert_that(limiter.check("client", now=11.0).allowed).is_true()


def test_keys_are_tracked_independently() -> None:
    """Separate client keys do not share the same budget."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

    assert_that(limiter.check("a", now=0.0).allowed).is_true()
    assert_that(limiter.check("b", now=0.0).allowed).is_true()
    assert_that(limiter.check("a", now=1.0).allowed).is_false()


def test_denied_request_does_not_extend_penalty() -> None:
    """A rejected request is not recorded, so the window still drains on time."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=10.0)

    limiter.check("client", now=0.0)
    limiter.check("client", now=8.0)  # denied, must not be recorded
    allowed_again = limiter.check("client", now=10.5)

    assert_that(allowed_again.allowed).is_true()


def test_reset_clears_recorded_hits() -> None:
    """Calling ``reset`` restores full capacity for every key."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

    limiter.check("client", now=0.0)
    limiter.reset()

    assert_that(limiter.check("client", now=1.0).allowed).is_true()


def test_rejects_non_positive_configuration() -> None:
    """Invalid limiter configuration raises ``ValueError``."""
    assert_that(SlidingWindowRateLimiter).raises(ValueError).when_called_with(
        max_requests=0, window_seconds=60.0
    )
    assert_that(SlidingWindowRateLimiter).raises(ValueError).when_called_with(
        max_requests=1, window_seconds=0.0
    )


def test_exposes_configuration_via_properties() -> None:
    """The limiter reports its configured ceiling and window."""
    limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=30.0)

    assert_that(limiter.max_requests).is_equal_to(5)
    assert_that(limiter.window_seconds).is_equal_to(30.0)


def test_uses_monotonic_clock_by_default() -> None:
    """Omitting ``now`` falls back to the monotonic clock without error."""
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)

    decision = limiter.check("client")

    assert_that(decision.allowed).is_true()


@pytest.mark.parametrize("bad_window", [-1.0, 0.0])
def test_window_must_be_positive(bad_window: float) -> None:
    """Non-positive windows are rejected regardless of the exact value."""
    assert_that(SlidingWindowRateLimiter).raises(ValueError).when_called_with(
        max_requests=1, window_seconds=bad_window
    )
