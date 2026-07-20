"""Tests for Prometheus instrumentation and the gated /metrics endpoint."""

from datetime import UTC, datetime

import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from sqlalchemy.orm import Session

import podex.metrics
from podex.api.deps import get_app_settings
from podex.config import Settings
from podex.metrics import build_metrics, observe_scheduler_tick
from podex.scheduler_runner import run_instrumented_tick
from podex.services.ops_metrics import (
    AlertDeliveryData,
    OperationalMetricsData,
    ReviewThroughputData,
)

_OPS_KEY = "test-ops-key"
_HEADERS = {"X-Ops-Key": _OPS_KEY}


def _enable_ops(client: TestClient) -> None:
    """Configure the internal ops key for a test app."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    app.dependency_overrides[get_app_settings] = lambda: Settings(
        ops_api_key=_OPS_KEY,
    )


def _registry(client: TestClient) -> CollectorRegistry:
    """Return the app's isolated metrics registry."""
    app = client.app
    if not isinstance(app, FastAPI):  # pragma: no cover - narrowed
        raise AssertionError
    registry: CollectorRegistry = app.state.metrics.registry
    return registry


def test_metrics_endpoint_requires_internal_access(client: TestClient) -> None:
    """Unconfigured reports 503; missing/wrong key reports 401; key gets 200."""
    assert_that(client.get("/metrics").status_code).is_equal_to(503)

    _enable_ops(client)
    assert_that(client.get("/metrics").status_code).is_equal_to(401)
    wrong = client.get("/metrics", headers={"X-Ops-Key": "wrong"})
    assert_that(wrong.status_code).is_equal_to(401)

    ok = client.get("/metrics", headers=_HEADERS)
    assert_that(ok.status_code).is_equal_to(200)
    assert_that(ok.headers["content-type"]).contains("text/plain")
    for family in (
        "podex_http_requests_total",
        "podex_http_request_duration_seconds",
        "podex_scheduler_ticks_total",
        "podex_scheduler_tick_duration_seconds",
        "podex_review_pending_items",
        "podex_alert_pending_events",
    ):
        assert_that(ok.text).contains(family)


def test_metrics_endpoint_absent_from_openapi_schema(client: TestClient) -> None:
    """/metrics stays out of the published OpenAPI contract."""
    schema = client.get("/openapi.json").json()
    assert_that(schema["paths"]).does_not_contain_key("/metrics")


def test_request_middleware_labels_route_template(
    client: TestClient,
    seeded_graph: object,
) -> None:
    """Requests count under the route template, never the raw path."""
    del seeded_graph
    _enable_ops(client)
    client.get("/health")
    client.get("/api/v2/podcasts/1")

    registry = _registry(client)
    health_count = registry.get_sample_value(
        "podex_http_requests_total",
        {"method": "GET", "route": "/health", "status": "200"},
    )
    assert_that(health_count).is_equal_to(1.0)
    templated_count = registry.get_sample_value(
        "podex_http_requests_total",
        {
            "method": "GET",
            "route": "/api/v2/podcasts/{podcast_id}",
            "status": "200",
        },
    )
    assert_that(templated_count).is_equal_to(1.0)
    latency_count = registry.get_sample_value(
        "podex_http_request_duration_seconds_count",
        {"method": "GET", "route": "/health", "status": "200"},
    )
    assert_that(latency_count).is_equal_to(1.0)


def test_request_middleware_labels_unmatched_routes(client: TestClient) -> None:
    """Requests that never match a route are labelled ``unmatched``."""
    client.get("/no-such-path")

    count = _registry(client).get_sample_value(
        "podex_http_requests_total",
        {"method": "GET", "route": "unmatched", "status": "404"},
    )
    assert_that(count).is_equal_to(1.0)


def test_scheduler_tick_instrumentation_records_success(
    db_session: Session,
) -> None:
    """A successful instrumented tick records a duration and outcome sample."""
    metrics = build_metrics()
    settings = Settings(
        scheduler_digest_interval_minutes=60,
        scheduler_retention_interval_minutes=60,
    )

    summary = run_instrumented_tick(
        db=db_session,
        settings=settings,
        digest_sender=None,
        metrics=metrics,
    )

    assert_that(summary.planned).is_greater_than_or_equal_to(2)
    success = metrics.registry.get_sample_value(
        "podex_scheduler_ticks_total",
        {"outcome": "success"},
    )
    assert_that(success).is_equal_to(1.0)
    duration_count = metrics.registry.get_sample_value(
        "podex_scheduler_tick_duration_seconds_count",
    )
    assert_that(duration_count).is_equal_to(1.0)


def test_scheduler_tick_instrumentation_records_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing tick records an error sample and re-raises the exception."""
    metrics = build_metrics()

    def explode(**kwargs: object) -> object:
        """Simulate a tick failure."""
        del kwargs
        raise RuntimeError("tick failed")

    monkeypatch.setattr("podex.scheduler_runner.run_tick", explode)

    with pytest.raises(RuntimeError, match="tick failed"):
        run_instrumented_tick(
            db=db_session,
            settings=Settings(),
            digest_sender=None,
            metrics=metrics,
        )

    errors = metrics.registry.get_sample_value(
        "podex_scheduler_ticks_total",
        {"outcome": "error"},
    )
    assert_that(errors).is_equal_to(1.0)


def test_observe_scheduler_tick_records_duration() -> None:
    """The tick observer records both the counter and histogram sample."""
    metrics = build_metrics()

    observe_scheduler_tick(metrics, duration_seconds=0.25, outcome="success")

    duration_sum = metrics.registry.get_sample_value(
        "podex_scheduler_tick_duration_seconds_sum",
    )
    assert_that(duration_sum).is_equal_to(0.25)


def test_ops_gauges_reflect_operational_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrape-time gauges mirror the operational-metrics service output."""
    _enable_ops(client)
    fake = OperationalMetricsData(
        measured_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        review=ReviewThroughputData(
            pending_items=7,
            decisions_last_24h=3,
            median_decision_minutes_last_24h=12.5,
        ),
        alerts=AlertDeliveryData(
            generated_events_last_24h=9,
            delivered_digests_last_24h=4,
            delivered_events_last_24h=6,
            pending_events=2,
        ),
    )
    monkeypatch.setattr(
        podex.metrics,
        "get_operational_metrics",
        lambda *, db: fake,
    )

    response = client.get("/metrics", headers=_HEADERS)

    assert_that(response.status_code).is_equal_to(200)
    registry = _registry(client)
    expected = {
        "podex_review_pending_items": 7.0,
        "podex_review_decisions_last_24h": 3.0,
        "podex_review_median_decision_minutes_last_24h": 12.5,
        "podex_alert_generated_events_last_24h": 9.0,
        "podex_alert_delivered_digests_last_24h": 4.0,
        "podex_alert_delivered_events_last_24h": 6.0,
        "podex_alert_pending_events": 2.0,
    }
    for name, value in expected.items():
        assert_that(registry.get_sample_value(name)).is_equal_to(value)


def test_registry_is_isolated_per_app() -> None:
    """Each create_app call builds its own registry; no cross-contamination."""
    from podex.main import create_app

    first = create_app()
    second = create_app()
    assert_that(first.state.metrics.registry).is_not_same_as(
        second.state.metrics.registry,
    )
