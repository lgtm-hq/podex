"""Protect the Python CI artifact transport configuration."""

from pathlib import Path

import pytest
import yaml
from assertpy import assert_that


@pytest.mark.parametrize("job_name", ["test", "test-coverage"])
def test_python_ci_appends_artifact_results_receiver(job_name: str) -> None:
    """Artifact uploads need the results service without replacing the preset."""
    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github/workflows/test-ci.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    options = workflow["jobs"][job_name]["with"]

    assert_that(options["egress-preset"]).is_equal_to("quality")
    assert_that(options.get("allowed-endpoints-mode")).is_equal_to("append")
    assert_that(str(options.get("allowed-endpoints", "")).split()).is_equal_to(
        ["results-receiver.actions.githubusercontent.com:443"]
    )


def test_aggregate_gate_rechecks_draft_conversion() -> None:
    """A same-head draft conversion must refresh fail-on-draft enforcement."""
    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github/workflows/all-checks-gate.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert_that(workflow["jobs"]["gate"]["with"]["fail-on-draft"]).is_true()
    assert_that(workflow["on"]["pull_request"]["types"]).contains(
        "converted_to_draft", "ready_for_review"
    )
