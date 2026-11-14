"""Launch inner pytest runs on an isolated copy of the PostgreSQL scaffolding.

Tests that need a whole pytest session (collection failures, signals) copy
``tests/conftest.py`` and ``tests/postgres/conftest.py`` (plus the pytest
configuration and Alembic files) into a ``pytester`` directory and run pytest
there, so offending test files never enter the real suite.

Every launch goes through ``launch_inner_pytest``. It requires the
``launches_pytest`` marker, explicit node ids and the ``pytester`` directory as
``cwd``, and sets ``PODEX_TESTS_INNER_RUN`` for the child. A run that sees that
variable refuses to launch another (here and at collection, see
``tests/conftest.py``), so inner runs never nest.
"""

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest

from tests.conftest import INNER_RUN_ENV, is_inner_run

BACKEND_DIR = Path(__file__).resolve().parents[1]
INNER_RUN_TIMEOUT_SECONDS = 120.0
_COPIED = (
    "pyproject.toml",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/postgres/__init__.py",
    "tests/postgres/conftest.py",
    "tests/postgres/signals.py",
)
_LINKED = ("alembic", "alembic.ini")


class InnerProcess(Protocol):
    """The parts of a launched inner pytest process the tests use."""

    returncode: int | None

    def poll(self) -> int | None:
        """Return the exit code, or ``None`` while running."""

    def send_signal(self, sig: int) -> None:
        """Send ``sig`` to the process."""

    def kill(self) -> None:
        """Kill the process."""

    def communicate(self, *, timeout: float | None = None) -> tuple[Any, Any]:
        """Wait for exit and return ``(stdout, stderr)``."""


@dataclass(frozen=True, slots=True)
class InnerResult:
    """Outcome of a finished inner pytest run."""

    returncode: int | None
    stdout: str
    stderr: str


def build_isolated_suite(pytester: pytest.Pytester, files: dict[str, str]) -> None:
    """Lay out the scaffolding plus ``files`` under ``pytester.path``.

    Args:
        pytester: The pytester whose directory receives the copy.
        files: Paths relative to ``tests/postgres`` mapped to their source.
    """
    for relative in _COPIED:
        target = pytester.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BACKEND_DIR / relative, target)
    for relative in _LINKED:
        (pytester.path / relative).symlink_to(BACKEND_DIR / relative)
    for relative, source in files.items():
        target = pytester.path / "tests" / "postgres" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)


def launch_inner_pytest(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    node_ids: Sequence[str],
    *options: str,
) -> InnerProcess:
    """Start pytest on explicit node ids in the isolated copy.

    Args:
        request: The launching test's request; it must carry
            ``@pytest.mark.launches_pytest``.
        pytester: Owner of the isolated copy, used as the child's ``cwd``.
        monkeypatch: Sets the nesting sentinel only while the child starts.
        node_ids: Explicit node ids, relative to ``pytester.path``.
        *options: Extra pytest options.

    Returns:
        The running child process.
    """
    if request.node.get_closest_marker("launches_pytest") is None:
        pytest.fail(
            "tests that launch pytest need @pytest.mark.launches_pytest",
            pytrace=False,
        )
    if is_inner_run(request.config):
        pytest.fail(
            f"refusing to launch pytest from an inner run ({INNER_RUN_ENV} is set)",
            pytrace=False,
        )
    if not node_ids:
        pytest.fail("inner runs need explicit node ids", pytrace=False)
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider"]
    with monkeypatch.context() as patch:
        # popen copies os.environ for the child; scope the sentinel to that.
        patch.setenv(INNER_RUN_ENV, "1")
        process: InnerProcess = pytester.popen(
            [*command, *options, *node_ids],
            cwd=pytester.path,
        )
    return process


def run_inner_pytest(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    node_ids: Sequence[str],
    *options: str,
) -> InnerResult:
    """Run pytest to completion on explicit node ids in the isolated copy.

    Returns:
        The exit code and decoded output.
    """
    process = launch_inner_pytest(request, pytester, monkeypatch, node_ids, *options)
    try:
        stdout, stderr = process.communicate(timeout=INNER_RUN_TIMEOUT_SECONDS)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    return InnerResult(
        returncode=process.returncode,
        stdout=stdout.decode(),
        stderr=stderr.decode(),
    )
