"""Guards around the PostgreSQL tests, checked without a database.

One test runs an inner pytest on an isolated copy of the scaffolding
(``tests/isolated_suite.py``), so an offending file never enters the real
suite; the rest call the guard functions and context managers directly.
"""

import re
import signal
from collections.abc import Iterator
from pathlib import Path
from types import FrameType

import pytest
from assertpy import assert_that

from tests.conftest import INNER_RUN_ENV, check_nested_launch
from tests.isolated_suite import (
    build_isolated_suite,
    launch_inner_pytest,
    run_inner_pytest,
)
from tests.postgres.conftest import (
    DATABASE_NAME_PATTERN,
    DATABASE_PREFIX,
    TEMPLATE_PREFIX,
    check_postgres_marker,
    new_database_name,
)
from tests.postgres.signals import (
    deferred_signals,
    exit_on_signal,
    sigterm_exits_session,
)

_MARKED = '''
import pytest

pytestmark = pytest.mark.postgres


def test_marked() -> None:
    """Carries the marker."""
'''

_UNMARKED = '''
def test_forgot_marker() -> None:
    """Lacks the marker."""
'''

# Deselects unmarked tests before any other modifyitems hook runs.
_DESELECTING_CONFTEST = '''
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Drop unmarked tests from the run."""
    dropped = [item for item in items if item.get_closest_marker("postgres") is None]
    config.hook.pytest_deselected(items=dropped)
    items[:] = [item for item in items if item not in dropped]
'''


@pytest.mark.launches_pytest
def test_unmarked_test_fails_collection_despite_a_deselecting_conftest(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nested conftest cannot deselect an unmarked test past the guard."""
    build_isolated_suite(
        pytester,
        {
            "test_marked.py": _MARKED,
            "nested/__init__.py": "",
            "nested/conftest.py": _DESELECTING_CONFTEST,
            "nested/test_unmarked.py": _UNMARKED,
        },
    )

    result = run_inner_pytest(
        request,
        pytester,
        monkeypatch,
        [
            "tests/postgres/test_marked.py::test_marked",
            "tests/postgres/nested/test_unmarked.py::test_forgot_marker",
        ],
        "--collect-only",
        "-m",
        "postgres",
    )

    assert_that(result.returncode).is_equal_to(pytest.ExitCode.USAGE_ERROR)
    assert_that(result.stderr).contains(
        "ERROR: tests under tests/postgres must carry @pytest.mark.postgres: "
        "tests/postgres/nested/test_unmarked.py::test_forgot_marker",
    )


def test_marker_guard_refuses_an_unmarked_test(request: pytest.FixtureRequest) -> None:
    """The guard rejects an unmarked test under the guarded directory."""
    with pytest.raises(pytest.UsageError, match="must carry @pytest.mark.postgres"):
        check_postgres_marker(request.node, directory=Path(__file__).parent)


def test_marker_guard_accepts_a_marked_test(request: pytest.FixtureRequest) -> None:
    """The guard accepts the same test once it carries the marker."""
    request.node.add_marker(pytest.mark.postgres)

    check_postgres_marker(request.node, directory=Path(__file__).parent)


def test_marker_guard_ignores_tests_elsewhere(request: pytest.FixtureRequest) -> None:
    """Tests outside the guarded directory need no marker."""
    check_postgres_marker(request.node, directory=Path(__file__).parent / "postgres")


@pytest.mark.launches_pytest
def test_launch_refused_inside_an_inner_run(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that sees the sentinel refuses to launch another pytest."""
    monkeypatch.setenv(INNER_RUN_ENV, "1")

    with pytest.raises(pytest.fail.Exception, match="refusing to launch pytest"):
        launch_inner_pytest(request, pytester, monkeypatch, ["tests"])


def test_launch_refused_without_the_marker(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only tests marked ``launches_pytest`` may launch pytest."""
    with pytest.raises(pytest.fail.Exception, match="launches_pytest"):
        launch_inner_pytest(request, pytester, monkeypatch, ["tests"])


@pytest.mark.launches_pytest
def test_launch_refused_without_node_ids(
    request: pytest.FixtureRequest,
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inner runs must name the tests they run."""
    with pytest.raises(pytest.fail.Exception, match="explicit node ids"):
        launch_inner_pytest(request, pytester, monkeypatch, [])


@pytest.mark.launches_pytest
def test_inner_run_refuses_to_collect_a_launcher(
    request: pytest.FixtureRequest,
) -> None:
    """Collection inside an inner run rejects tests that launch pytest."""
    with pytest.raises(pytest.UsageError, match="nested inner runs are refused"):
        check_nested_launch(request.node, inner_run=True)
    check_nested_launch(request.node, inner_run=False)


@pytest.mark.parametrize("prefix", [DATABASE_PREFIX, TEMPLATE_PREFIX])
def test_generated_names_match_the_documented_pattern(prefix: str) -> None:
    """The recovery pattern in CONTRIBUTING.md matches every generated name."""
    name = new_database_name(prefix)

    assert_that(re.fullmatch(DATABASE_NAME_PATTERN, name)).is_not_none()


@pytest.fixture(name="sigterm_recorder")
def sigterm_recorder_fixture() -> Iterator[list[int]]:
    """Install a recording SIGTERM handler; restore the previous one after.

    Yields:
        The signals the recorder received.
    """
    received: list[int] = []

    def record(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)

    previous = signal.signal(signal.SIGTERM, record)
    try:
        yield received
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_deferred_signals_hold_sigint_until_the_block_ends() -> None:
    """SIGINT inside the block is delivered once, after the block finished."""
    finished: list[bool] = []

    with pytest.raises(KeyboardInterrupt):
        with deferred_signals():
            signal.raise_signal(signal.SIGINT)
            finished.append(True)

    assert_that(finished).is_equal_to([True])
    assert_that(signal.getsignal(signal.SIGINT)).is_same_as(signal.default_int_handler)


def test_deferred_signals_deliver_sigterm_to_the_previous_handler(
    sigterm_recorder: list[int],
) -> None:
    """SIGTERM inside the block reaches the restored handler after it."""
    with deferred_signals():
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)
        assert_that(sigterm_recorder).is_empty()

    assert_that(sigterm_recorder).is_equal_to([signal.SIGTERM])


@pytest.fixture(name="default_sigterm")
def default_sigterm_fixture() -> Iterator[None]:
    """Run with the default SIGTERM handler; restore the previous one after."""
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@pytest.mark.usefixtures("default_sigterm")
def test_sigterm_exit_is_installed_and_restored() -> None:
    """The session handler replaces the default and puts it back."""
    with sigterm_exits_session():
        assert_that(signal.getsignal(signal.SIGTERM)).is_same_as(exit_on_signal)

    assert_that(signal.getsignal(signal.SIGTERM)).is_same_as(signal.SIG_DFL)


@pytest.mark.usefixtures("default_sigterm")
def test_sigterm_exit_keeps_a_handler_installed_meanwhile() -> None:
    """A handler another plugin installs later is not overwritten on exit."""

    def later(_signum: int, _frame: FrameType | None) -> None:
        """Stand in for another plugin's handler."""

    with sigterm_exits_session():
        signal.signal(signal.SIGTERM, later)

    assert_that(signal.getsignal(signal.SIGTERM)).is_same_as(later)


def test_sigterm_exit_leaves_an_existing_handler_alone(
    sigterm_recorder: list[int],
) -> None:
    """Over a non-default handler the session handler is not installed."""
    before = signal.getsignal(signal.SIGTERM)

    with sigterm_exits_session():
        assert_that(signal.getsignal(signal.SIGTERM)).is_same_as(before)

    assert_that(signal.getsignal(signal.SIGTERM)).is_same_as(before)
    assert_that(sigterm_recorder).is_empty()
