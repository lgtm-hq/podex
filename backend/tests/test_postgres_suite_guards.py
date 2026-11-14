"""Guards that keep PostgreSQL tests on PostgreSQL, checked without a database.

Each test runs an inner pytest on an isolated copy of the PostgreSQL test
scaffolding (``tests/isolated_suite.py``), so offending files never enter the
real suite.
"""

import re

import pytest
from assertpy import assert_that

from tests.isolated_suite import build_isolated_suite
from tests.postgres.conftest import (
    DATABASE_NAME_PATTERN,
    DATABASE_PREFIX,
    TEMPLATE_PREFIX,
    new_database_name,
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

# Never connected to: the fixtures under test fail before any connection.
_UNREACHABLE_URL = "postgresql+psycopg://nobody@127.0.0.1:1/none"


@pytest.mark.parametrize(
    "args",
    [("-m", "postgres"), ()],
    ids=["postgres-lane-selection", "plain-run"],
)
def test_unmarked_test_in_postgres_dir_fails_collection(
    pytester: pytest.Pytester,
    args: tuple[str, ...],
) -> None:
    """An unmarked test there fails collection instead of being deselected."""
    build_isolated_suite(
        pytester,
        {"test_marked.py": _MARKED, "test_unmarked.py": _UNMARKED},
    )

    result = pytester.runpytest_subprocess(
        "tests", "--collect-only", "-p", "no:cacheprovider", *args
    )

    assert_that(result.ret).is_equal_to(pytest.ExitCode.USAGE_ERROR)
    result.stderr.fnmatch_lines(
        [
            "ERROR: tests under tests/postgres must carry @pytest.mark.postgres: "
            "tests/postgres/test_unmarked.py::test_forgot_marker",
        ],
    )


def test_marked_tests_in_postgres_dir_collect(pytester: pytest.Pytester) -> None:
    """Control: the same layout with only marked tests collects cleanly."""
    build_isolated_suite(pytester, {"test_marked.py": _MARKED})

    result = pytester.runpytest_subprocess(
        "tests", "--collect-only", "-p", "no:cacheprovider", "-m", "postgres"
    )

    assert_that(result.ret).is_equal_to(pytest.ExitCode.OK)
    result.stdout.fnmatch_lines(["*1 test collected*"])


@pytest.mark.parametrize("fixture", ["db_session", "client", "seeded_graph"])
def test_sqlite_fixtures_fail_in_postgres_dir(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    fixture: str,
) -> None:
    """A PostgreSQL test cannot fall back to the SQLite fixtures."""
    source = f'''
import pytest

pytestmark = pytest.mark.postgres


def test_uses_sqlite({fixture}: object) -> None:
    """Asks for a SQLite fixture."""
'''
    build_isolated_suite(pytester, {"test_sqlite.py": source})
    monkeypatch.setenv("PODEX_DATABASE__URL", _UNREACHABLE_URL)

    result = pytester.runpytest_subprocess(
        "tests", "-p", "no:cacheprovider", "--require-postgres"
    )

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        [
            f"*{fixture} is the SQLite fixture; tests under tests/postgres use "
            "the postgres fixture*",
        ],
    )


@pytest.mark.parametrize("prefix", [DATABASE_PREFIX, TEMPLATE_PREFIX])
def test_generated_names_match_the_documented_pattern(prefix: str) -> None:
    """The recovery pattern in CONTRIBUTING.md matches every generated name."""
    assert_that(
        re.fullmatch(DATABASE_NAME_PATTERN, new_database_name(prefix))
    ).is_not_none()
