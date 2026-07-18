"""Unit tests for the sliding-window rate limiter (in-memory + Redis backends)."""

import fakeredis
import pytest
from assertpy import assert_that

from podex.config import Settings
from podex.services.limiter import RateLimiter, SlidingWindowRateLimiter
from podex.services.limiter_factory import build_rate_limiter
from podex.services.redis_limiter import RedisSlidingWindowRateLimiter


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


def test_stale_keys_are_swept_to_bound_memory() -> None:
    """Buckets for clients that stopped sending are eventually dropped.

    Without the periodic sweep, every distinct key ever checked would keep an
    entry in the internal store forever (unbounded growth under IP rotation).
    """
    limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=10.0)

    for index in range(1023):
        limiter.check(f"key-{index}", now=0.0)
    # The 1024th check triggers the sweep; every key last seen at t=0 is
    # stale relative to the window ending at t=100.
    limiter.check("fresh", now=100.0)

    assert_that(limiter._hits).is_length(1)
    assert_that(limiter._hits).contains_key("fresh")


@pytest.mark.parametrize("bad_window", [-1.0, 0.0])
def test_window_must_be_positive(bad_window: float) -> None:
    """Non-positive windows are rejected regardless of the exact value."""
    assert_that(SlidingWindowRateLimiter).raises(ValueError).when_called_with(
        max_requests=1, window_seconds=bad_window
    )


def _make_redis_limiter(
    *, max_requests: int = 2, window_seconds: float = 60.0
) -> RedisSlidingWindowRateLimiter:
    """Return a Redis-backed limiter wired to a fresh in-memory fake."""
    client = fakeredis.FakeStrictRedis()
    return RedisSlidingWindowRateLimiter(
        client,
        max_requests=max_requests,
        window_seconds=window_seconds,
        key_prefix="test:ratelimit",
    )


def test_redis_limiter_allows_requests_under_the_limit() -> None:
    """Redis-backed limiter admits requests below the ceiling."""
    limiter = _make_redis_limiter(max_requests=3, window_seconds=60.0)

    first = limiter.check("client", now=0.0)
    second = limiter.check("client", now=1.0)

    assert_that(first.allowed).is_true()
    assert_that(first.remaining).is_equal_to(2)
    assert_that(second.allowed).is_true()
    assert_that(second.remaining).is_equal_to(1)


def test_redis_limiter_blocks_requests_over_the_limit() -> None:
    """Redis-backed limiter returns the same denial hints as the in-memory one."""
    limiter = _make_redis_limiter(max_requests=2, window_seconds=60.0)

    limiter.check("client", now=0.0)
    limiter.check("client", now=1.0)
    denied = limiter.check("client", now=2.0)

    assert_that(denied.allowed).is_false()
    assert_that(denied.remaining).is_equal_to(0)
    assert_that(denied.limit).is_equal_to(2)
    assert_that(denied.retry_after).is_equal_to(58.0)


def test_redis_limiter_window_slides_to_free_capacity() -> None:
    """Aged-out timestamps free capacity in the Redis-backed limiter too."""
    limiter = _make_redis_limiter(max_requests=1, window_seconds=10.0)

    assert_that(limiter.check("client", now=0.0).allowed).is_true()
    assert_that(limiter.check("client", now=5.0).allowed).is_false()
    assert_that(limiter.check("client", now=11.0).allowed).is_true()


def test_redis_limiter_keys_are_tracked_independently() -> None:
    """Distinct client keys have independent budgets in Redis."""
    limiter = _make_redis_limiter(max_requests=1, window_seconds=60.0)

    assert_that(limiter.check("a", now=0.0).allowed).is_true()
    assert_that(limiter.check("b", now=0.0).allowed).is_true()
    assert_that(limiter.check("a", now=1.0).allowed).is_false()


def test_redis_limiter_denied_request_does_not_extend_penalty() -> None:
    """Rejected requests are not recorded, matching the in-memory contract."""
    limiter = _make_redis_limiter(max_requests=1, window_seconds=10.0)

    limiter.check("client", now=0.0)
    limiter.check("client", now=8.0)
    allowed_again = limiter.check("client", now=10.5)

    assert_that(allowed_again.allowed).is_true()


def test_redis_limiter_two_instances_share_state() -> None:
    """Two limiter instances wired to the same Redis enforce a shared budget.

    This is the whole point of the shared store: additional workers must not
    multiply the effective limit.
    """
    client = fakeredis.FakeStrictRedis()
    worker_a = RedisSlidingWindowRateLimiter(
        client, max_requests=2, window_seconds=60.0, key_prefix="shared"
    )
    worker_b = RedisSlidingWindowRateLimiter(
        client, max_requests=2, window_seconds=60.0, key_prefix="shared"
    )

    assert_that(worker_a.check("client", now=0.0).allowed).is_true()
    assert_that(worker_b.check("client", now=1.0).allowed).is_true()
    assert_that(worker_a.check("client", now=2.0).allowed).is_false()
    assert_that(worker_b.check("client", now=3.0).allowed).is_false()


def test_redis_limiter_reset_clears_recorded_hits() -> None:
    """``reset`` wipes every key under the limiter's prefix."""
    limiter = _make_redis_limiter(max_requests=1, window_seconds=60.0)

    limiter.check("client", now=0.0)
    limiter.reset()

    assert_that(limiter.check("client", now=1.0).allowed).is_true()


def test_redis_limiter_uses_wall_clock_by_default() -> None:
    """Omitting ``now`` falls back to ``time.time`` without error."""
    limiter = _make_redis_limiter(max_requests=1, window_seconds=60.0)

    decision = limiter.check("client")

    assert_that(decision.allowed).is_true()


def test_redis_limiter_exposes_configuration_via_properties() -> None:
    """The Redis limiter reports its configured ceiling and window."""
    limiter = _make_redis_limiter(max_requests=5, window_seconds=30.0)

    assert_that(limiter.max_requests).is_equal_to(5)
    assert_that(limiter.window_seconds).is_equal_to(30.0)


def test_redis_limiter_rejects_non_positive_configuration() -> None:
    """Invalid configuration is rejected the same way as the in-memory backend."""
    client = fakeredis.FakeStrictRedis()

    assert_that(RedisSlidingWindowRateLimiter).raises(ValueError).when_called_with(
        client, max_requests=0, window_seconds=60.0
    )
    assert_that(RedisSlidingWindowRateLimiter).raises(ValueError).when_called_with(
        client, max_requests=1, window_seconds=0.0
    )


def test_redis_limiter_matches_in_memory_decisions_over_a_sequence() -> None:
    """Both backends must reach the same allow/deny verdict at each step.

    Documents the equivalence the middleware relies on: swapping the backend
    must not change externally observable behaviour.
    """
    memory = SlidingWindowRateLimiter(max_requests=3, window_seconds=10.0)
    shared = _make_redis_limiter(max_requests=3, window_seconds=10.0)

    times = [0.0, 1.0, 2.0, 3.0, 4.0, 9.5, 10.5, 11.5, 12.5]
    for now in times:
        memory_decision = memory.check("client", now=now)
        redis_decision = shared.check("client", now=now)
        assert_that(redis_decision.allowed).is_equal_to(memory_decision.allowed)


def test_build_rate_limiter_returns_in_memory_when_redis_url_unset() -> None:
    """An empty ``rate_limit_redis_url`` selects the in-memory backend."""
    settings = Settings(
        rate_limit_max_requests=4,
        rate_limit_window_seconds=15.0,
        rate_limit_redis_url="",
    )

    limiter = build_rate_limiter(settings)

    assert_that(limiter).is_instance_of(SlidingWindowRateLimiter)
    assert_that(limiter.max_requests).is_equal_to(4)
    assert_that(limiter.window_seconds).is_equal_to(15.0)


def test_build_rate_limiter_returns_redis_backend_when_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty ``rate_limit_redis_url`` selects the shared backend.

    The factory imports ``redis`` lazily; we swap in a fakeredis-backed
    ``from_url`` so no real network connection is attempted.
    """
    import redis

    client = fakeredis.FakeStrictRedis()

    def fake_from_url(url: str, **_: object) -> fakeredis.FakeStrictRedis:
        """Return a shared fakeredis client regardless of URL."""
        assert_that(url).is_equal_to("redis://localhost:6379/0")
        return client

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))

    settings = Settings(
        rate_limit_max_requests=2,
        rate_limit_window_seconds=60.0,
        rate_limit_redis_url="redis://localhost:6379/0",
        rate_limit_redis_prefix="podex-test:ratelimit",
    )

    limiter = build_rate_limiter(settings)

    assert_that(limiter).is_instance_of(RedisSlidingWindowRateLimiter)
    assert_that(limiter.max_requests).is_equal_to(2)
    decision = limiter.check("factory-client", now=0.0)
    assert_that(decision.allowed).is_true()


def test_both_backends_satisfy_the_rate_limiter_protocol() -> None:
    """Both backends structurally implement the ``RateLimiter`` protocol."""
    memory: RateLimiter = SlidingWindowRateLimiter(
        max_requests=1, window_seconds=60.0
    )
    shared: RateLimiter = _make_redis_limiter(max_requests=1, window_seconds=60.0)

    assert_that(isinstance(memory, RateLimiter)).is_true()
    assert_that(isinstance(shared, RateLimiter)).is_true()
