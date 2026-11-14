"""Offline tests for cancellable, capacity-bounded system feed DNS."""

from __future__ import annotations

import asyncio
import socket
from queue import Full, Queue
from threading import Event
from unittest.mock import Mock

import pytest
from assertpy import assert_that

from podex.services.feed_dns import FeedDnsError, resolve_feed_host


def test_system_dns_resolves_ipv4_and_ipv6_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return deduplicated system answers through the daemon resolver adapter."""
    resolver = Mock(
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                6,
                "",
                ("2606:4700:4700::1111", 443, 0, 0),
            ),
        ]
    )
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    addresses = asyncio.run(resolve_feed_host("feeds.example", 443))
    assert_that(addresses).is_equal_to(["8.8.8.8", "2606:4700:4700::1111"])
    resolver.assert_called_once_with("feeds.example", 443, type=socket.SOCK_STREAM)


@pytest.mark.parametrize("error", [socket.gaierror, UnicodeError])
def test_system_dns_failure_propagates(
    error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolution failures return promptly and release the worker capacity."""
    slots: Queue[None] = Queue(maxsize=1)
    monkeypatch.setattr("podex.services.feed_dns._RESOLVER_SLOTS", slots)
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=error("not found")))
    with pytest.raises(FeedDnsError, match="DNS resolution failed") as caught:
        asyncio.run(resolve_feed_host("feeds.example", 443))
    assert_that(caught.value.failure_reason).is_equal_to("dns_failure")
    assert_that(slots.empty()).is_true()


def test_abandoned_dns_keeps_capacity_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deadline returns without joining DNS; stalled work cannot grow unbounded."""
    slots: Queue[None] = Queue(maxsize=1)
    started = Event()
    release = Event()
    finished = Event()
    monkeypatch.setattr("podex.services.feed_dns._RESOLVER_SLOTS", slots)

    def resolve(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        """Simulate uncancellable libc resolution without any network work."""
        del args, kwargs
        started.set()
        release.wait(timeout=2)
        finished.set()
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    async def exercise() -> None:
        """Cancel one resolver while verifying excess work fails immediately."""
        task = asyncio.create_task(resolve_feed_host("feeds.example", 443))
        async with asyncio.timeout(1):
            while not started.is_set():
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(FeedDnsError, match="busy") as caught:
            await resolve_feed_host("other.example", 443)
        assert_that(caught.value.failure_reason).is_equal_to("dns_busy")
        with pytest.raises(Full):
            slots.put_nowait(None)

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        assert_that(finished.wait(timeout=2)).is_true()


def test_dns_worker_start_failure_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread exhaustion fails explicitly without leaking a resolver slot."""
    slots: Queue[None] = Queue(maxsize=1)
    monkeypatch.setattr("podex.services.feed_dns._RESOLVER_SLOTS", slots)
    worker = Mock()
    worker.start.side_effect = RuntimeError("thread unavailable")
    monkeypatch.setattr("podex.services.feed_dns.Thread", Mock(return_value=worker))
    with pytest.raises(FeedDnsError, match="worker could not start") as caught:
        asyncio.run(resolve_feed_host("feeds.example", 443))
    assert_that(caught.value.failure_reason).is_equal_to("dns_worker_unavailable")
    assert_that(slots.empty()).is_true()
