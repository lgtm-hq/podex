"""Tests for the v2 RFC 9457 problem-details error bodies."""

import logging

import pytest
from assertpy import assert_that
from fastapi.testclient import TestClient

from podex.api.v2.errors import (
    PROBLEM_JSON_MEDIA_TYPE,
    PROBLEM_TYPE_BASE,
    STATUS_CODE_TO_ERROR_CODE,
    error_code_for_status,
)
from podex.config import Settings
from podex.main import create_app
from podex.middleware import REQUEST_ID_HEADER


def test_not_found_returns_problem_details(client: TestClient) -> None:
    """404 responses use the RFC 9457 body with the ``not_found`` code."""
    response = client.get("/api/v2/podcasts/9999")

    assert_that(response.status_code).is_equal_to(404)
    assert_that(response.headers["content-type"]).is_equal_to(
        PROBLEM_JSON_MEDIA_TYPE,
    )
    body = response.json()
    assert_that(body["type"]).is_equal_to(f"{PROBLEM_TYPE_BASE}not_found")
    assert_that(body["title"]).is_equal_to("Not Found")
    assert_that(body["status"]).is_equal_to(404)
    assert_that(body["code"]).is_equal_to("not_found")
    assert_that(body["detail"]).is_equal_to("Podcast not found")
    assert_that(body["request_id"]).is_equal_to(response.headers[REQUEST_ID_HEADER])


def test_validation_error_returns_details(client: TestClient) -> None:
    """422 responses include per-field details in the envelope."""
    response = client.get("/api/v2/podcasts", params={"limit": 0})

    assert_that(response.status_code).is_equal_to(422)
    assert_that(response.headers["content-type"]).is_equal_to(
        PROBLEM_JSON_MEDIA_TYPE,
    )
    body = response.json()
    assert_that(body["status"]).is_equal_to(422)
    assert_that(body["code"]).is_equal_to("unprocessable_entity")
    assert_that(body["errors"]).is_not_empty()
    loc_paths = [tuple(item["loc"]) for item in body["errors"]]
    assert_that(loc_paths).contains(("query", "limit"))


def test_unhandled_exception_returns_500_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unhandled route errors are wrapped in an opaque 500 envelope."""
    app = create_app(Settings(rate_limit_enabled=False))

    async def _boom() -> None:
        """Route handler that always fails."""
        raise RuntimeError("boom")

    app.add_api_route("/boom", _boom, methods=["GET"])

    with TestClient(app, raise_server_exceptions=False) as client:
        with caplog.at_level(logging.INFO, logger="podex.access"):
            response = client.get("/boom", headers={REQUEST_ID_HEADER: "err-1"})

    assert_that(response.status_code).is_equal_to(500)
    body = response.json()
    assert_that(body["code"]).is_equal_to("internal_server_error")
    assert_that(body["detail"]).is_equal_to("Internal server error.")
    assert_that(body["request_id"]).is_equal_to("err-1")


def test_error_code_for_status_uses_registered_map() -> None:
    """The status-to-code map yields registered strings for known codes."""
    for status_code, code in STATUS_CODE_TO_ERROR_CODE.items():
        assert_that(error_code_for_status(status_code)).is_equal_to(code)


def test_error_code_for_status_falls_back_to_generic() -> None:
    """Unknown status codes get a stable ``http_<code>`` fallback."""
    assert_that(error_code_for_status(418)).is_equal_to("http_418")


def test_http_exception_with_structured_detail_preserves_code(
    client: TestClient,
) -> None:
    """Structured ``HTTPException.detail`` dicts propagate ``code``/``details``."""
    from fastapi import HTTPException

    from podex.main import app as _base_app

    del _base_app
    app = create_app(Settings(rate_limit_enabled=False))

    async def _custom() -> None:
        """Raise a structured HTTPException to exercise the handler."""
        raise HTTPException(
            status_code=409,
            detail={
                "code": "custom_conflict",
                "message": "already exists",
                "details": [
                    {"loc": ["body", "slug"], "msg": "duplicate", "type": "conflict"},
                ],
            },
        )

    app.add_api_route("/custom-conflict", _custom, methods=["GET"])

    with TestClient(app) as test_client:
        response = test_client.get("/custom-conflict")

    assert_that(response.status_code).is_equal_to(409)
    error = response.json()
    assert_that(error["code"]).is_equal_to("custom_conflict")
    assert_that(error["detail"]).is_equal_to("already exists")
    assert_that(error["errors"][0]["loc"]).is_equal_to(["body", "slug"])
