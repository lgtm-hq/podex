"""Protect the time limits that stop a hung backend test job (#846)."""

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

REPO_ROOT = Path(__file__).resolve().parents[2]

# Job id -> timeout-minutes. The two reusable callers pass the pinned
# reusable-test-python input; the two PostgreSQL jobs set it on the job.
REUSABLE_LIMITS = {"test": 20, "test-coverage": 20}
DIRECT_LIMITS = {"test-postgres-migrations": 30, "test-postgres-application": 30}


def _test_ci_jobs() -> dict[str, Any]:
    workflow_path = REPO_ROOT / ".github/workflows/test-ci.yml"
    workflow: dict[str, Any] = yaml.safe_load(
        workflow_path.read_text(encoding="utf-8"),
    )
    jobs: dict[str, Any] = workflow["jobs"]
    return jobs


def _faulthandler_timeout() -> object:
    pyproject = tomllib.loads(
        (REPO_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"),
    )
    return pyproject["tool"]["pytest"]["ini_options"].get("faulthandler_timeout")


@pytest.mark.parametrize(("job_name", "minutes"), REUSABLE_LIMITS.items())
def test_reusable_test_callers_pass_a_job_limit(job_name: str, minutes: int) -> None:
    """The Python matrix and coverage jobs use the reusable's timeout input."""
    job = _test_ci_jobs()[job_name]

    assert_that(job["uses"]).contains("/reusable-test-python.yml@")
    assert_that(job["with"]).contains_key("timeout-minutes")
    assert_that(job["with"]["timeout-minutes"]).is_equal_to(minutes)


@pytest.mark.parametrize(("job_name", "minutes"), DIRECT_LIMITS.items())
def test_postgres_jobs_set_a_job_limit(job_name: str, minutes: int) -> None:
    """The PostgreSQL lanes bound their set-up and test steps."""
    job = _test_ci_jobs()[job_name]

    assert_that(job).contains_key("timeout-minutes")
    assert_that(job["timeout-minutes"]).is_equal_to(minutes)


def test_faulthandler_dumps_before_any_job_limit() -> None:
    """A stuck test's stack reaches the log before its job is stopped."""
    timeout = _faulthandler_timeout()

    assert_that(timeout).is_instance_of(int)
    assert_that(timeout).is_greater_than(0)
    for minutes in (*REUSABLE_LIMITS.values(), *DIRECT_LIMITS.values()):
        assert_that(timeout).is_less_than(minutes * 60)


def test_a_stuck_test_is_reported_not_stopped_by_pytest() -> None:
    """The stack dump only prints; no per-test limit ends a test."""
    pyproject = tomllib.loads(
        (REPO_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"),
    )
    options = pyproject["tool"]["pytest"]["ini_options"]

    assert_that(options.get("faulthandler_exit_on_timeout", False)).is_false()
    assert_that(options).does_not_contain_key("timeout")
