"""Tests for the core-path HTTP load harness."""

from podex.services.load_testing import RequestResult, build_workload, summarize_results


def test_build_workload_keeps_ingestion_mutations_opt_in() -> None:
    """Verify default-style workloads contain no durable ingestion triggers."""
    workload = build_workload(
        search_requests=2,
        pipeline_reads=1,
        ingestion_triggers=0,
    )

    assert len(workload) == 3
    assert all(request.method == "GET" for request in workload)


def test_summarize_results_reports_threshold_inputs_by_scenario() -> None:
    """Verify summary metrics expose success, error, latency, and scenario counts."""
    summary = summarize_results(
        [
            RequestResult("public_search", 10.0, 200, True),
            RequestResult("public_search", 20.0, 429, False),
            RequestResult("pipeline_activity", 30.0, 200, True),
        ]
    )

    assert summary["requests"] == 3
    assert summary["failed"] == 1
    assert summary["p95_ms"] == 30.0
    assert summary["scenarios"] == {"public_search": 2, "pipeline_activity": 1}
