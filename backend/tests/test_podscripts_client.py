"""Tests for bounded Podscripts request retries."""

import httpx
import pytest
from assertpy import assert_that

from podex.services.podscripts_client import fetch_podscripts_html


def test_fetch_podscripts_html_retries_transient_http_status() -> None:
    """Verify transient provider failures are retried before succeeding."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code=503, request=request)
        return httpx.Response(status_code=200, text="<main>ok</main>", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        html = fetch_podscripts_html(
            client=client,
            url="https://podscripts.co/podcasts/example",
            retry_delay_seconds=0,
        )

    assert_that(html).contains("ok")
    assert_that(attempts).is_equal_to(2)


def test_fetch_podscripts_html_does_not_retry_not_found() -> None:
    """Verify permanent not-found responses fail immediately."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code=404, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            fetch_podscripts_html(
                client=client,
                url="https://podscripts.co/podcasts/missing",
                retry_delay_seconds=0,
            )

    assert_that(attempts).is_equal_to(1)
