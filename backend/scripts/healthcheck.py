#!/usr/bin/env python3
"""Probe API liveness while allowing the shared image's scheduler command."""

from http.client import HTTPConnection, HTTPException
from pathlib import Path


def scheduler_is_running(proc_root: Path) -> bool:
    """Find the scheduler module through shell, Python option and init wrappers.

    Args:
        proc_root: Linux process filesystem to inspect.

    Returns:
        Whether any visible process command line contains the scheduler module.
    """
    for command_line in proc_root.glob("[0-9]*/cmdline"):
        try:
            if b"podex.scheduler_runner" in command_line.read_bytes():
                return True
        except OSError:
            # A process may exit during enumeration or belong to another user.
            continue
    return False


def is_healthy(proc_root: Path = Path("/proc")) -> bool:
    """Check the default API port unless this container runs the scheduler.

    Args:
        proc_root: Linux process filesystem to inspect for the scheduler.

    Returns:
        Whether the scheduler is present or exact /health serves HTTP 200.
    """
    if scheduler_is_running(proc_root):
        return True

    connection = HTTPConnection("127.0.0.1", 8000, timeout=2)
    try:
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(0 if is_healthy() else 1)
