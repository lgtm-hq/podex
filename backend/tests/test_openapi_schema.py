"""Guard against drift between the app schema and committed OpenAPI artifact."""

from pathlib import Path

from assertpy import assert_that

from podex.openapi import render_openapi_json

OPENAPI_ARTIFACT = (
    Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"
)

REGEN_HINT = (
    "frontend/openapi.json is stale. Regenerate it with "
    "`cd backend && uv run python -m podex.openapi ../frontend/openapi.json` "
    "and `cd frontend && bun run generate:api`, then commit the result."
)


def test_committed_openapi_matches_app() -> None:
    """The committed OpenAPI artifact matches the live FastAPI schema."""
    assert_that(OPENAPI_ARTIFACT.exists()).described_as(REGEN_HINT).is_true()
    assert_that(OPENAPI_ARTIFACT.read_text(encoding="utf-8")).described_as(
        REGEN_HINT
    ).is_equal_to(render_openapi_json())
