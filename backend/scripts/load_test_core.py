#!/usr/bin/env python3
"""Exercise core public search and operator ingestion HTTP paths.

Run against a deployed environment with suitable rate-limit settings. Pipeline
trigger requests are opt-in because they create durable ingestion run records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from time import perf_counter

import httpx

from podex.services.load_testing import (
    RequestResult,
    ScenarioRequest,
    build_workload,
    summarize_results,
)


async def run_request(
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    request: ScenarioRequest,
) -> RequestResult:
    """Execute one bounded-concurrency load-test request."""
    async with semaphore:
        started = perf_counter()
        try:
            response = await client.request(request.method, request.path)
            duration_ms = (perf_counter() - started) * 1000
            return RequestResult(
                scenario=request.scenario,
                duration_ms=duration_ms,
                status_code=response.status_code,
                succeeded=response.status_code == request.expected_status,
            )
        except httpx.HTTPError as error:
            return RequestResult(
                scenario=request.scenario,
                duration_ms=(perf_counter() - started) * 1000,
                status_code=None,
                succeeded=False,
                error=str(error),
            )


async def execute_workload(
    *,
    base_url: str,
    api_key: str | None,
    concurrency: int,
    requests: list[ScenarioRequest],
) -> list[RequestResult]:
    """Execute a workload against a Podex HTTP deployment."""
    headers = {"X-API-Key": api_key} if api_key else {}
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=30.0,
    ) as client:
        return await asyncio.gather(
            *[
                run_request(client=client, semaphore=semaphore, request=request)
                for request in requests
            ]
        )


def parse_args() -> argparse.Namespace:
    """Parse load-test command-line options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--search-requests", type=int, default=100)
    parser.add_argument("--pipeline-reads", type=int, default=25)
    parser.add_argument(
        "--ingestion-triggers",
        type=int,
        default=0,
        help="Opt-in POST requests that create durable ingestion run records.",
    )
    parser.add_argument("--max-p95-ms", type=float, default=750.0)
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    return parser.parse_args()


def main() -> int:
    """Execute the configured load test and evaluate performance thresholds."""
    args = parse_args()
    workload = build_workload(
        search_requests=max(0, args.search_requests),
        pipeline_reads=max(0, args.pipeline_reads),
        ingestion_triggers=max(0, args.ingestion_triggers),
    )
    results = asyncio.run(
        execute_workload(
            base_url=args.base_url,
            api_key=args.api_key,
            concurrency=max(1, args.concurrency),
            requests=workload,
        )
    )
    summary = summarize_results(results)
    output = {
        "summary": summary,
        "failures": [asdict(result) for result in results if not result.succeeded][:10],
    }
    print(json.dumps(output, indent=2))
    if (
        float(summary["p95_ms"]) > args.max_p95_ms
        or float(summary["error_rate"]) > args.max_error_rate
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
