"""Signal handling that keeps PostgreSQL fixture cleanup from being skipped.

- ``deferred_signals`` holds SIGINT and SIGTERM while a database is created and
  recorded as owned, and while it is dropped, then delivers them, so a signal
  cannot land between ``CREATE DATABASE`` and the cleanup that drops it, or
  stop a ``DROP DATABASE`` half way.
- ``sigterm_exits_session`` turns SIGTERM into ``pytest.exit`` while fixture
  databases exist, so teardown runs as it does on Ctrl-C.

Both act only in the main thread (the only one that can install handlers) and
leave handlers someone else installed alone.
"""

import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any

import pytest

DEFERRED_SIGNALS = (signal.SIGINT, signal.SIGTERM)

Handler = Callable[[int, FrameType | None], Any] | int | signal.Handlers


def _python_handlers() -> dict[int, Handler] | None:
    """Return the current SIGINT/SIGTERM handlers if Python can restore them."""
    if threading.current_thread() is not threading.main_thread():
        return None
    handlers: dict[int, Handler] = {}
    for signum in DEFERRED_SIGNALS:
        handler = signal.getsignal(signum)
        if handler is None:
            return None
        handlers[signum] = handler
    return handlers


@contextmanager
def deferred_signals() -> Iterator[None]:
    """Hold SIGINT and SIGTERM until the block ends, then deliver them.

    Signals received inside the block are recorded instead of handled. On exit,
    even when the block raised, the previous handlers are restored and each
    recorded signal is raised again, once, in arrival order.
    """
    previous = _python_handlers()
    if previous is None:
        yield
        return
    received: list[int] = []

    def record(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)

    for signum in previous:
        signal.signal(signum, record)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        for signum in dict.fromkeys(received):
            signal.raise_signal(signum)


def exit_on_signal(signum: int, _frame: FrameType | None) -> None:
    """End the pytest session in order so fixture teardown drops databases."""
    pytest.exit(f"terminated by signal {signum}", returncode=128 + signum)


@contextmanager
def sigterm_exits_session() -> Iterator[None]:
    """Turn SIGTERM into ``pytest.exit`` for the duration of the block.

    Installed only from the main thread and only over the default handler. On
    exit the default is restored only if ``exit_on_signal`` is still the
    handler, so a handler another plugin installed meanwhile is kept.
    """
    if (
        threading.current_thread() is not threading.main_thread()
        or signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    ):
        yield
        return
    signal.signal(signal.SIGTERM, exit_on_signal)
    try:
        yield
    finally:
        if signal.getsignal(signal.SIGTERM) is exit_on_signal:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
