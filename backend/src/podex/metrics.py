"""Prometheus instrumentation: registry factory, middleware, and /metrics.

Each :func:`podex.main.create_app` call builds a fresh
:class:`AppMetrics` (its own ``CollectorRegistry``) so repeated app
construction in tests never trips duplicate-registration errors and
samples never cross-contaminate between test apps. Production runs
uvicorn single-process on Railway, so a plain per-process registry is
sufficient — no multiprocess mode.

The scheduler runner process reuses the same factory for its tick
instrumentation (see :mod:`podex.scheduler_runner`).
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response, status
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from podex.api.deps import AppSettings, DbSession
from podex.config import Settings
from podex.services.ops_metrics import get_operational_metrics

UNMATCHED_ROUTE = "unmatched"

RequestResponder = Callable[[Request], Awaitable[Response]]


@dataclass(frozen=True, slots=True)
class AppMetrics:
    """Prometheus collectors bound to one registry.

    Attributes:
        registry: The isolated registry all collectors are registered on.
        http_requests_total: Request counter labelled by method, route
            template, and status code.
        http_request_duration_seconds: Request latency histogram with the
            same labels as the counter.
        scheduler_ticks_total: Scheduler tick counter labelled by outcome
            (``success``/``error``).
        scheduler_tick_duration_seconds: Scheduler tick duration histogram.
        review_pending_items: Review items awaiting a decision.
        review_decisions_last_24h: Review decisions made in the last 24h.
        review_median_decision_minutes: Median minutes-to-decision over the
            last 24h (NaN when no decisions were made).
        alert_generated_events_last_24h: Alert events generated in the
            last 24h.
        alert_delivered_digests_last_24h: Digests delivered in the last 24h.
        alert_delivered_events_last_24h: Alert events delivered via digests
            in the last 24h.
        alert_pending_events: Alert events not yet attached to a digest.
    """

    registry: CollectorRegistry
    http_requests_total: Counter
    http_request_duration_seconds: Histogram
    scheduler_ticks_total: Counter
    scheduler_tick_duration_seconds: Histogram
    review_pending_items: Gauge
    review_decisions_last_24h: Gauge
    review_median_decision_minutes: Gauge
    alert_generated_events_last_24h: Gauge
    alert_delivered_digests_last_24h: Gauge
    alert_delivered_events_last_24h: Gauge
    alert_pending_events: Gauge


def build_metrics() -> AppMetrics:
    """Create all Podex collectors on a fresh, isolated registry.

    Returns:
        The collectors plus the registry they are registered on.
    """
    registry = CollectorRegistry()
    return AppMetrics(
        registry=registry,
        http_requests_total=Counter(
            "podex_http_requests_total",
            "HTTP requests processed, by method, route template, and status.",
            labelnames=("method", "route", "status"),
            registry=registry,
        ),
        http_request_duration_seconds=Histogram(
            "podex_http_request_duration_seconds",
            "HTTP request latency, by method, route template, and status.",
            labelnames=("method", "route", "status"),
            registry=registry,
        ),
        scheduler_ticks_total=Counter(
            "podex_scheduler_ticks_total",
            "Scheduler ticks executed, by outcome.",
            labelnames=("outcome",),
            registry=registry,
        ),
        scheduler_tick_duration_seconds=Histogram(
            "podex_scheduler_tick_duration_seconds",
            "Scheduler tick duration in seconds.",
            registry=registry,
        ),
        review_pending_items=Gauge(
            "podex_review_pending_items",
            "Review items currently awaiting a decision.",
            registry=registry,
        ),
        review_decisions_last_24h=Gauge(
            "podex_review_decisions_last_24h",
            "Review decisions made in the last 24 hours.",
            registry=registry,
        ),
        review_median_decision_minutes=Gauge(
            "podex_review_median_decision_minutes_last_24h",
            "Median minutes from creation to decision over the last 24 hours.",
            registry=registry,
        ),
        alert_generated_events_last_24h=Gauge(
            "podex_alert_generated_events_last_24h",
            "Account alert events generated in the last 24 hours.",
            registry=registry,
        ),
        alert_delivered_digests_last_24h=Gauge(
            "podex_alert_delivered_digests_last_24h",
            "Account digests delivered in the last 24 hours.",
            registry=registry,
        ),
        alert_delivered_events_last_24h=Gauge(
            "podex_alert_delivered_events_last_24h",
            "Account alert events delivered via digests in the last 24 hours.",
            registry=registry,
        ),
        alert_pending_events=Gauge(
            "podex_alert_pending_events",
            "Account alert events not yet attached to a digest.",
            registry=registry,
        ),
    )


def observe_scheduler_tick(
    metrics: AppMetrics,
    *,
    duration_seconds: float,
    outcome: str,
) -> None:
    """Record one scheduler tick sample.

    Args:
        metrics: The collectors to record the sample on.
        duration_seconds: Wall-clock duration of the tick.
        outcome: ``"success"`` or ``"error"``.
    """
    metrics.scheduler_ticks_total.labels(outcome=outcome).inc()
    metrics.scheduler_tick_duration_seconds.observe(duration_seconds)


def update_operational_gauges(*, metrics: AppMetrics, db: Session) -> None:
    """Refresh the ops gauges from the operational-metrics service.

    Called at scrape time so gauge values always reflect the current
    database state rather than a stale snapshot.

    Args:
        metrics: The collectors whose gauges are refreshed.
        db: Database session used to compute the operational metrics.
    """
    operational = get_operational_metrics(db=db)
    metrics.review_pending_items.set(operational.review.pending_items)
    metrics.review_decisions_last_24h.set(operational.review.decisions_last_24h)
    median_minutes = operational.review.median_decision_minutes_last_24h
    metrics.review_median_decision_minutes.set(
        median_minutes if median_minutes is not None else float("nan"),
    )
    metrics.alert_generated_events_last_24h.set(
        operational.alerts.generated_events_last_24h,
    )
    metrics.alert_delivered_digests_last_24h.set(
        operational.alerts.delivered_digests_last_24h,
    )
    metrics.alert_delivered_events_last_24h.set(
        operational.alerts.delivered_events_last_24h,
    )
    metrics.alert_pending_events.set(operational.alerts.pending_events)


def _route_template(request: Request) -> str:
    """Return the full route template for a matched request.

    ``scope["route"].path_format`` on recent FastAPI versions only covers
    the innermost router (included routers are mounted, so ``/api/v2`` is
    absent from ``/podcasts/{podcast_id}``). The mount prefix is recovered
    by rendering the template with the matched path params and stripping
    that suffix from the raw request path.

    Args:
        request: The processed request (after routing).

    Returns:
        The full path template (e.g. ``/api/v2/podcasts/{podcast_id}``),
        or ``unmatched`` when no route matched.
    """
    route = request.scope.get("route")
    path_format: str | None = getattr(route, "path_format", None)
    if not path_format:
        return UNMATCHED_ROUTE
    path = str(request.scope.get("path", ""))
    rendered = path_format
    path_params = request.scope.get("path_params") or {}
    for name, value in path_params.items():
        rendered = rendered.replace("{" + name + "}", str(value))
    if path != rendered and path.endswith(rendered):
        return path[: -len(rendered)] + path_format
    return path_format


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record a counter increment and latency sample per HTTP request.

    The route label uses the matched route's path template (e.g.
    ``/api/v2/podcasts/{podcast_id}``) — never the raw request path — so
    label cardinality stays bounded. Requests that finish without a route
    match (404s, rate-limited rejections) are labelled ``unmatched``.

    Args:
        app: The wrapped ASGI application.
        metrics: The collectors samples are recorded on.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        metrics: AppMetrics,
    ) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next: RequestResponder) -> Response:
        """Time the downstream handler and record request samples.

        Args:
            request: The incoming request being processed.
            call_next: Callable that invokes the remaining middleware/handler
                chain.

        Returns:
            Response: The downstream response, unchanged.
        """
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            labels = {
                "method": request.method,
                "route": _route_template(request),
                "status": str(status_code),
            }
            self._metrics.http_requests_total.labels(**labels).inc()
            self._metrics.http_request_duration_seconds.labels(**labels).observe(
                duration,
            )


def _require_internal_access(*, request: Request, settings: Settings) -> None:
    """Reject the request unless the configured ops key is presented.

    Mirrors the gating used by the ops console endpoints
    (:mod:`podex.api.v2.ops`): the surface is disabled (503) until
    ``PODEX_OPS_API_KEY`` is configured, and requires the key in the
    ``X-Ops-Key`` header (401 otherwise).

    Args:
        request: The incoming request carrying the ``X-Ops-Key`` header.
        settings: Runtime settings providing the expected key.

    Raises:
        HTTPException: 503 when unconfigured, 401 on a missing/wrong key.
    """
    if not settings.ops_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Metrics endpoint is not configured",
        )
    if request.headers.get("X-Ops-Key") != settings.ops_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ops authentication required",
        )


def get_prometheus_metrics(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> Response:
    """Serve the Prometheus text exposition for this process.

    Internally gated: never publicly reachable unauthenticated. Ops
    gauges are recomputed from the database at scrape time.

    Args:
        request: The incoming scrape request.
        db: Request-scoped database session for the gauge computation.
        settings: Resolved application settings (provides the ops key).

    Returns:
        Response: Prometheus text-format exposition of the app registry.
    """
    _require_internal_access(request=request, settings=settings)
    metrics: AppMetrics = request.app.state.metrics
    update_operational_gauges(metrics=metrics, db=db)
    db.commit()
    return Response(
        content=generate_latest(metrics.registry),
        media_type=CONTENT_TYPE_LATEST,
    )
