"""Tests for the bounded retry loop used for public Podscripts pages."""

import logging
from dataclasses import dataclass, field

import httpx
import pytest
from assertpy import assert_that

from podex.services import podscripts_client
from podex.services.podscripts_client import fetch_podscripts_html

_URL = "https://podscripts.co/podcasts/example-show"


@dataclass
class _ScriptedHandler:
    """Serve a fixed sequence of outcomes, one per request."""

    outcomes: list[int | Exception]
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Return the next status or raise the next transport error."""
        outcome = self.outcomes[len(self.requests)]
        self.requests.append(request)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, text=f"body-{outcome}")


@pytest.fixture(name="sleeps")
def sleeps_fixture(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry waits instead of sleeping."""
    recorded: list[float] = []
    monkeypatch.setattr(podscripts_client.time, "sleep", recorded.append)
    return recorded


def _client(handler: _ScriptedHandler) -> httpx.Client:
    """Return a client whose transport is the scripted handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_retries_retryable_status_then_succeeds(
    status: int,
    sleeps: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timeouts, rate limits and server errors are retried with linear backoff."""
    handler = _ScriptedHandler([status, status, 200])
    caplog.set_level(logging.WARNING, logger=podscripts_client.__name__)

    with _client(handler) as client:
        body = fetch_podscripts_html(
            client=client,
            url=_URL,
            retry_delay_seconds=2.0,
        )

    assert_that(body).is_equal_to("body-200")
    assert_that(handler.requests).is_length(3)
    assert_that(sleeps).is_equal_to([2.0, 4.0])
    warnings = [record.getMessage() for record in caplog.records]
    assert_that(warnings).is_equal_to(
        [
            f"Transient Podscripts request failure; retrying {_URL} in 2.0s",
            f"Transient Podscripts request failure; retrying {_URL} in 4.0s",
        ],
    )


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_raises_permanent_4xx_without_retry(
    status: int,
    sleeps: list[float],
) -> None:
    """A permanent client error is raised on the first attempt."""
    handler = _ScriptedHandler([status, 200])

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError) as caught:
        fetch_podscripts_html(client=client, url=_URL, retry_delay_seconds=1.0)

    assert_that(caught.value.response.status_code).is_equal_to(status)
    assert_that(handler.requests).is_length(1)
    assert_that(sleeps).is_empty()


def test_raises_last_retryable_status_when_attempts_run_out(
    sleeps: list[float],
) -> None:
    """The final retryable status is raised once every attempt is spent."""
    handler = _ScriptedHandler([503, 429])

    with _client(handler) as client, pytest.raises(httpx.HTTPStatusError) as caught:
        fetch_podscripts_html(
            client=client,
            url=_URL,
            retry_delay_seconds=0.5,
            max_attempts=2,
        )

    assert_that(caught.value.response.status_code).is_equal_to(429)
    assert_that(handler.requests).is_length(2)
    assert_that(sleeps).is_equal_to([0.5])


def test_retries_transport_error_then_succeeds(sleeps: list[float]) -> None:
    """A transport failure before the last attempt is retried."""
    request = httpx.Request("GET", _URL)
    handler = _ScriptedHandler([httpx.ConnectError("refused", request=request), 200])

    with _client(handler) as client:
        body = fetch_podscripts_html(client=client, url=_URL, retry_delay_seconds=1.5)

    assert_that(body).is_equal_to("body-200")
    assert_that(sleeps).is_equal_to([1.5])


def test_raises_after_last_transport_error(sleeps: list[float]) -> None:
    """The last transport failure propagates unchanged after the final attempt."""
    request = httpx.Request("GET", _URL)
    first: Exception = httpx.ConnectError("refused", request=request)
    last: Exception = httpx.ReadTimeout("slow", request=request)
    handler = _ScriptedHandler([first, first, last])

    with _client(handler) as client, pytest.raises(httpx.ReadTimeout) as caught:
        fetch_podscripts_html(client=client, url=_URL, retry_delay_seconds=1.0)

    assert_that(caught.value).is_same_as(last)
    assert_that(handler.requests).is_length(3)
    assert_that(sleeps).is_equal_to([1.0, 2.0])


@pytest.mark.parametrize("max_attempts", [0, -1])
def test_rejects_zero_attempts(max_attempts: int, sleeps: list[float]) -> None:
    """A non-positive attempt budget is rejected before any request is sent."""
    handler = _ScriptedHandler([200])

    with _client(handler) as client, pytest.raises(ValueError) as caught:
        fetch_podscripts_html(
            client=client,
            url=_URL,
            retry_delay_seconds=1.0,
            max_attempts=max_attempts,
        )

    assert_that(str(caught.value)).is_equal_to("max_attempts must be at least one")
    assert_that(handler.requests).is_empty()
    assert_that(sleeps).is_empty()
