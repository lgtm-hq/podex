"""Reusable workload and result metrics for core-path load checks."""

import math
from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True, slots=True)
class ScenarioRequest:
    """One HTTP action in a load-test workload."""

    scenario: str
    method: str
    path: str
    expected_status: int


@dataclass(frozen=True, slots=True)
class RequestResult:
    """Observed response timing and status for one request."""

    scenario: str
    duration_ms: float
    status_code: int | None
    succeeded: bool
    error: str | None = None


class LoadSummary(TypedDict):
    """Aggregate latency and success measures for an executed workload."""

    requests: int
    succeeded: int
    failed: int
    error_rate: float
    p50_ms: float
    p95_ms: float
    scenarios: dict[str, int]


def build_workload(
    *,
    search_requests: int,
    pipeline_reads: int,
    ingestion_triggers: int,
) -> list[ScenarioRequest]:
    """Build the requested search and ingestion path workload."""
    requests = [
        ScenarioRequest(
            scenario="public_search",
            method="GET",
            path="/api/v2/search?q=book&limit=10",
            expected_status=200,
        )
        for _ in range(search_requests)
    ]
    requests.extend(
        ScenarioRequest(
            scenario="pipeline_activity",
            method="GET",
            path="/api/v2/ops/pipelines?run_limit=10&job_limit=10",
            expected_status=200,
        )
        for _ in range(pipeline_reads)
    )
    requests.extend(
        ScenarioRequest(
            scenario="pipeline_trigger",
            method="POST",
            path="/api/v2/ops/pipelines/run",
            expected_status=202,
        )
        for _ in range(ingestion_triggers)
    )
    return requests


def summarize_results(results: list[RequestResult]) -> LoadSummary:
    """Calculate latency and success metrics for an executed workload."""
    if not results:
        return {
            "requests": 0,
            "succeeded": 0,
            "failed": 0,
            "error_rate": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "scenarios": {},
        }
    durations = sorted(result.duration_ms for result in results)
    succeeded = sum(result.succeeded for result in results)
    p50_index = max(0, math.ceil(len(durations) * 0.50) - 1)
    p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
    scenarios: dict[str, int] = {}
    for result in results:
        scenarios[result.scenario] = scenarios.get(result.scenario, 0) + 1
    return {
        "requests": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "error_rate": (len(results) - succeeded) / len(results),
        "p50_ms": round(durations[p50_index], 2),
        "p95_ms": round(durations[p95_index], 2),
        "scenarios": scenarios,
    }
