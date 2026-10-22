"""Protect the Python CI artifact transport under blocking egress."""

from pathlib import Path

import pytest
import yaml
from assertpy import assert_that


@pytest.mark.parametrize("job_name", ["test", "test-coverage"])
def test_python_ci_appends_artifact_results_receiver(job_name: str) -> None:
    """Artifact uploads need the results service without replacing the preset."""
    workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/test-ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    options = workflow["jobs"][job_name]["with"]

    assert_that(options["egress-policy"]).is_equal_to("block")
    assert_that(options["egress-preset"]).is_equal_to("quality")
    assert_that(options.get("allowed-endpoints-mode")).is_equal_to("append")
    assert_that(str(options.get("allowed-endpoints", "")).split()).contains(
        "results-receiver.actions.githubusercontent.com:443"
    )
