"""Deadline-compatible DNS resolution with bounded background resolver work."""

from __future__ import annotations

import asyncio
import socket
from concurrent.futures import Future
from queue import Full, Queue
from threading import Thread

# libc resolution cannot be cancelled. Bound abandoned work independently of
# scheduler concurrency and never wait for these daemon threads at loop shutdown.
_RESOLVER_SLOTS: Queue[None] = Queue(maxsize=4)


async def resolve_feed_host(host: str, port: int) -> list[str]:
    """Resolve all TCP addresses without blocking cancellation of the caller.

    Args:
        host: ASCII hostname to resolve.
        port: Destination port.

    Returns:
        All resolved IPv4/IPv6 address strings.

    Raises:
        OSError: Resolution failed or all resolver slots are occupied.
    """
    try:
        _RESOLVER_SLOTS.put_nowait(None)
    except Full:
        raise OSError("Feed DNS resolver is busy") from None
    slots = _RESOLVER_SLOTS
    result: Future[list[str]] = Future()

    def resolve() -> None:
        """Resolve in a bounded daemon thread, releasing capacity on completion."""
        try:
            if result.set_running_or_notify_cancel():
                try:
                    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                    result.set_result(
                        list(dict.fromkeys(str(r[4][0]) for r in records))
                    )
                except (OSError, UnicodeError):
                    result.set_exception(OSError("Feed DNS resolution failed"))
        finally:
            slots.get_nowait()

    try:
        Thread(target=resolve, name="feed-dns", daemon=True).start()
    except RuntimeError:
        slots.get_nowait()
        raise OSError("Feed DNS worker could not start") from None
    return await asyncio.wrap_future(result)
