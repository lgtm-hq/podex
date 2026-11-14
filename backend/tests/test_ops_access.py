"""Shared authentication contract for ops and Prometheus metrics."""

import logging
import secrets
from unittest.mock import patch

import httpx
import pytest
from assertpy import assert_that
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from podex.api.deps import get_app_settings
from podex.config import Settings
from tests.conftest import enable_ops

_OPS_KEY = "shared-ops-test-key"
_PATHS = ("/api/v2/ops/metrics", "/metrics")


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize(
    ("credential", "expected_status"),
    [(_OPS_KEY, 200), ("wrong-key-same-size", 401)],
    ids=["valid", "invalid"],
)
def test_ops_and_metrics_use_constant_time_verifier(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    path: str,
    credential: str,
    expected_status: int,
) -> None:
    """Both surfaces compare configured credentials through the byte boundary."""
    enable_ops(client, api_key=_OPS_KEY)

    with (
        caplog.at_level(logging.DEBUG),
        patch("secrets.compare_digest", wraps=secrets.compare_digest) as compare,
    ):
        response = client.get(path, headers={"X-Ops-Key": credential})

    assert_that(response.status_code).is_equal_to(expected_status)
    compare.assert_called_once()
    operands = compare.call_args.args
    assert_that(operands).is_length(2)
    for operand in operands:
        assert_that(operand).is_instance_of(bytes)
    # The configured key is the second operand, so timing follows its length.
    assert_that(operands).is_equal_to((credential.encode(), _OPS_KEY.encode()))
    assert_that(response.text + caplog.text).does_not_contain(_OPS_KEY, credential)


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/v2/ops/metrics", "Ops console is not configured"),
        ("/metrics", "Metrics endpoint is not configured"),
    ],
)
@pytest.mark.parametrize("credential", [None, _OPS_KEY])
def test_unconfigured_ops_access(
    client: TestClient,
    path: str,
    detail: str,
    credential: str | None,
) -> None:
    """Disabled surfaces preserve their 503 detail regardless of credentials."""
    enable_ops(client, api_key="")
    headers = {} if credential is None else {"X-Ops-Key": credential}

    response = client.get(path, headers=headers)

    assert_that(response.status_code).is_equal_to(503)
    assert_that(response.json()["detail"]).is_equal_to(detail)
    assert_that(response.json()["code"]).is_equal_to("service_unavailable")
    assert_that(response.headers["content-type"]).is_equal_to(
        "application/problem+json",
    )


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize(
    "credential",
    [None, b"", b"bad-short", b"x" * 128, "clé".encode(), b"\xff"],
    ids=["missing", "empty", "shorter", "longer", "unicode", "latin1-char"],
)
def test_invalid_ops_access_is_opaque(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    path: str,
    credential: bytes | None,
) -> None:
    """Invalid credentials return the existing 401 without leaking key values."""
    enable_ops(client, api_key=_OPS_KEY)
    headers = [] if credential is None else [(b"X-Ops-Key", credential)]

    with caplog.at_level(logging.DEBUG):
        response = client.get(path, headers=headers)

    assert_that(response.status_code).is_equal_to(401)
    assert_that(response.json()["detail"]).is_equal_to("Ops authentication required")
    assert_that(response.json()["code"]).is_equal_to("unauthorized")
    assert_that(response.headers["content-type"]).is_equal_to(
        "application/problem+json",
    )
    output = response.text + caplog.text
    assert_that(output).does_not_contain(_OPS_KEY)
    if credential:
        assert_that(output).does_not_contain(credential.decode("latin-1"))


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize(
    ("configured_key", "credential"),
    [
        ("k\udcff", "wrong"),
        ("k\ud800", "wrong"),
        ("k\udc00", "wrong"),
        # The test client sends b"k\xc3\xbf", which the app reads as Latin-1.
        # Re-encoded as UTF-8 it equals this key's surrogate-escaped bytes.
        ("k\udcc3\udc83\udcc2\udcbf", "k\u00ff"),
    ],
    ids=["escaped-byte", "high-surrogate", "low-surrogate", "byte-collision"],
)
async def test_unencodable_configured_key_never_authenticates(
    client: TestClient,
    path: str,
    configured_key: str,
    credential: str,
) -> None:
    """A configured key without a valid UTF-8 encoding yields 401, never 200 or 500."""
    # Settings now reject this at load. Deliberately bypass validation to retain
    # coverage of the verifier's defensive handling of an injected invalid key.
    settings = Settings().model_copy(update={"ops_api_key": SecretStr(configured_key)})
    app = client.app
    if not isinstance(app, FastAPI):
        raise AssertionError("Expected the FastAPI test fixture")
    app.dependency_overrides[get_app_settings] = lambda: settings
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="http://testserver",
    ) as ops_client:
        response = await ops_client.get(
            path,
            headers=[(b"X-Ops-Key", credential.encode())],
        )

    assert_that(response.status_code).is_equal_to(401)
    assert_that(response.json()["detail"]).is_equal_to("Ops authentication required")
