"""Guard against drift between the app schema and committed OpenAPI artifact."""

from collections import Counter
from pathlib import Path

from assertpy import assert_that
from fastapi.routing import iter_route_contexts

from podex.main import app
from podex.openapi import render_openapi_json

OPENAPI_ARTIFACT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"

REGEN_HINT = (
    "frontend/openapi.json is stale. Regenerate it with "
    "`cd backend && uv run python -m podex.openapi ../frontend/openapi.json` "
    "and `cd frontend && bun run generate:api`, then commit the result."
)

# Sign-in routes and every ``/me*`` route, as (method, path) pairs.
ACCOUNT_ROUTES: tuple[tuple[str, str], ...] = (
    ("POST", "/api/v2/auth/magic-link/request"),
    ("POST", "/api/v2/auth/magic-link/verify"),
    ("GET", "/api/v2/auth/login"),
    ("GET", "/api/v2/auth/callback"),
    ("POST", "/api/v2/auth/logout"),
    ("GET", "/api/v2/me"),
    ("DELETE", "/api/v2/me"),
    ("GET", "/api/v2/me/saves"),
    ("PUT", "/api/v2/me/saves/{media_id}"),
    ("DELETE", "/api/v2/me/saves/{media_id}"),
    ("GET", "/api/v2/me/follows"),
    ("PUT", "/api/v2/me/follows/{podcast_id}"),
    ("DELETE", "/api/v2/me/follows/{podcast_id}"),
    ("GET", "/api/v2/me/alerts"),
    ("POST", "/api/v2/me/alerts"),
    ("PATCH", "/api/v2/me/alerts/{rule_id}"),
    ("DELETE", "/api/v2/me/alerts/{rule_id}"),
    ("POST", "/api/v2/me/alerts/evaluate"),
    ("GET", "/api/v2/me/digests"),
    ("POST", "/api/v2/me/digests/send"),
    ("GET", "/api/v2/me/preferences"),
    ("PATCH", "/api/v2/me/preferences"),
    ("GET", "/api/v2/me/subscription"),
    ("POST", "/api/v2/me/subscription/checkout"),
    ("GET", "/api/v2/me/export"),
)


def test_committed_openapi_matches_app() -> None:
    """The committed OpenAPI artifact matches the live FastAPI schema."""
    assert_that(OPENAPI_ARTIFACT.exists()).described_as(REGEN_HINT).is_true()
    assert_that(OPENAPI_ARTIFACT.read_text(encoding="utf-8")).described_as(
        REGEN_HINT
    ).is_equal_to(render_openapi_json())


def test_account_routes_registered() -> None:
    """Each auth and account route is registered exactly once on the app."""
    registered: Counter[tuple[str, str | None]] = Counter(
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        for method in context.methods or ()
    )

    assert_that(ACCOUNT_ROUTES).is_length(25)
    for method, path in ACCOUNT_ROUTES:
        assert_that(registered[(method, path)]).described_as(
            f"{method} {path}",
        ).is_equal_to(1)
