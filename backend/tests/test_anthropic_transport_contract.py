"""Contract tests for the Anthropic SDK at its HTTP transport boundary.

Each test builds a real ``anthropic.Anthropic`` client whose ``httpx2`` client
uses a ``MockTransport``. The SDK therefore serializes the request, applies its
headers and retry policy, maps error statuses to its own exception classes and
decodes response bodies exactly as in production; only the socket layer is
replaced. Connection setup, name resolution and datagram sends through the
``socket`` module are blocked for the whole module, so no test can reach the
network, and only a dummy credential is used.

The expected request contract (default models, system prompts, message
templates) is written here independently of the code under test, so changing
any of them in production fails a test until it is updated deliberately.
"""

import hashlib
import json
import os
import socket
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest
from assertpy import assert_that

from podex.services.llm_extraction import LLMExtractor
from podex.services.llm_transcript_cleanup import TranscriptCleaner

_API_KEY = "test-key-not-a-credential"
_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_TRANSCRIPT = "word " * 60
_SDK_MAX_RETRIES = anthropic.DEFAULT_MAX_RETRIES

_EXTRACTION_MODEL = "claude-opus-4-8"
_CLEANUP_MODEL = "claude-opus-4-8"

# Prompt text may live only in podex.services.prompts_default (enforced by
# .semgrep/prompt-boundary.yaml), so the exact system prompts are pinned by the
# SHA-256 of their UTF-8 text plus a few anchoring phrases, not by a copy. After
# a deliberate prompt change, recompute the digest with
# hashlib.sha256(prompt.encode()).hexdigest().
_EXTRACTION_SYSTEM_SHA256 = (
    "411634344ad2de93bf8c6756d52ab4e476d6638e51ae20f46cfa443c1f35b8b7"
)
_EXTRACTION_SYSTEM_ANCHORS = (
    "<output_schema>",
    "<security>",
    "Return ONLY a valid JSON array.",
)
_CLEANUP_SYSTEM_SHA256 = (
    "c50aa63b22cf6ced6fb0771b3bd5b41ed63c19583dd8fecd9d211a7233ef2c34"
)
_CLEANUP_SYSTEM_ANCHORS = (
    "DO NOT:",
    "Only include corrections that were actually made.",
    "untrusted third-party data",
)
_CLEANUP_USER_MESSAGE = (
    "CONTEXT:\n"
    "Podcast: Example Show\n"
    "Host: Host Example\n"
    "Guest: Guest Example\n"
    "Known terms and names: podex, alembic\n"
    "\n"
    "TRANSCRIPT TO CLEAN:\n"
    f"<transcript>\n{_TRANSCRIPT}\n</transcript>\n"
    "\n"
    "Please analyze the transcript and fix any transcription errors. "
    "Return as JSON."
)

Handler = Callable[[httpx2.Request], httpx2.Response]


@dataclass
class _Provider:
    """Scripted provider endpoint behind the SDK's transport seam.

    Attributes:
        handler: Produces the response (or raises) for each request.
        requests: Every request the SDK sent through the transport.
    """

    handler: Handler
    requests: list[httpx2.Request] = field(default_factory=list)

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Record the request and answer it with the scripted handler."""
        self.requests.append(request)
        return self.handler(request)

    def bodies(self) -> list[dict[str, Any]]:
        """Return the decoded JSON bodies of all recorded requests."""
        return [json.loads(request.content) for request in self.requests]


@pytest.fixture(name="network_attempts", autouse=True)
def _blocked_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[object]]:
    """Fail the test on any outbound network use through ``socket``.

    Blocks connection setup (``socket.connect``, ``connect_ex``,
    ``create_connection``), name resolution (``getaddrinfo``) and datagram
    sends (``sendto``, ``sendmsg``). Code that bypasses the ``socket`` module,
    such as direct ``_socket`` use, is outside this guarantee.

    The SDK wraps transport exceptions in ``APIConnectionError``, which the
    services swallow, so the guard also records attempts and asserts on them
    after the test instead of relying on the exception alone.
    """
    attempts: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append((args, kwargs))
        raise ConnectionRefusedError("network access is blocked in this module")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket.socket, "sendmsg", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield attempts
    assert_that(attempts).described_as("real network attempts").is_empty()


@pytest.fixture(autouse=True)
def _isolated_sdk_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient Anthropic settings such as a base URL or real key."""
    for name in list(os.environ):
        if name.startswith("ANTHROPIC_"):
            monkeypatch.delenv(name)


@pytest.fixture(name="sleeps")
def _recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record SDK retry back-off delays instead of waiting for them."""
    delays: list[float] = []
    monkeypatch.setattr(time, "sleep", delays.append)
    return delays


def _client(provider: _Provider) -> anthropic.Anthropic:
    """Build the real SDK client with production defaults and a mock transport.

    Only the credential and the HTTP transport differ from the client the
    services construct when none is injected; retries, timeouts, base URL and
    response validation keep the SDK defaults.
    """
    return anthropic.Anthropic(
        api_key=_API_KEY,
        http_client=httpx2.Client(transport=httpx2.MockTransport(provider)),
    )


def _message(text: str) -> dict[str, Any]:
    """Return a representative Messages API success body."""
    return {
        "id": "msg_contract",
        "type": "message",
        "role": "assistant",
        "model": _EXTRACTION_MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 40},
    }


def _api_error(status: int, error_type: str) -> Handler:
    """Return a handler that answers with a provider error envelope."""
    body = {
        "type": "error",
        "error": {"type": error_type, "message": f"{error_type} for test"},
    }
    return lambda _request: httpx2.Response(status, json=body)


def _extract(client: anthropic.Anthropic) -> list[str]:
    """Run the extractor through ``client`` and return its errors."""
    result = LLMExtractor(client=client).extract_from_transcript(
        [{"text": _TRANSCRIPT}],
    )
    assert_that(result.items).is_empty()
    errors: list[str] = result.errors
    return errors


def _clean(client: anthropic.Anthropic) -> list[str]:
    """Run the cleaner through ``client`` and return its errors."""
    result = TranscriptCleaner(client=client).cleanup_transcript(_TRANSCRIPT)
    assert_that(result.cleaned_text).is_equal_to(_TRANSCRIPT)
    assert_that(result.corrections_made).is_empty()
    errors: list[str] = result.errors
    return errors


_SERVICES = pytest.mark.parametrize(
    "run_service",
    [_extract, _clean],
    ids=["extraction", "cleanup"],
)


def _assert_messages_request(request: httpx2.Request) -> None:
    """Assert the transport-level shape every service request shares."""
    assert_that(request.method).is_equal_to("POST")
    assert_that(str(request.url)).is_equal_to(_MESSAGES_URL)
    assert_that(request.headers["content-type"]).is_equal_to("application/json")
    assert_that(request.headers.get("anthropic-version")).is_not_empty()
    assert_that(request.headers.get("authorization", "")).contains(_API_KEY)


def _assert_system_prompt(
    system: object,
    *,
    sha256: str,
    anchors: tuple[str, ...],
) -> None:
    """Assert the exact system prompt text by digest and anchoring phrases."""
    assert_that(system).is_instance_of(str)
    text = str(system)
    assert_that(text).contains(*anchors)
    assert_that(hashlib.sha256(text.encode()).hexdigest()).described_as(
        "SHA-256 of the system prompt",
    ).is_equal_to(sha256)


def test_extraction_sends_messages_request_and_decodes_items() -> None:
    """The extractor's request and response pass through the real SDK."""
    payload = json.dumps(
        [
            {"title": "Dune", "type": "book", "creator": "Frank Herbert"},
            {"title": "Arrival", "type": "movie", "year": 2016, "confidence": 0.7},
        ],
    )
    provider = _Provider(
        lambda _request: httpx2.Response(200, json=_message(payload)),
    )

    result = LLMExtractor(client=_client(provider)).extract_from_transcript(
        [{"text": _TRANSCRIPT}],
    )

    assert_that(result.errors).is_empty()
    assert_that([item.title for item in result.items]).is_equal_to(
        ["Dune", "Arrival"],
    )
    assert_that(result.items[1].year).is_equal_to(2016)
    assert_that(provider.requests).is_length(1)
    _assert_messages_request(provider.requests[0])
    body = provider.bodies()[0]
    _assert_system_prompt(
        body.pop("system", None),
        sha256=_EXTRACTION_SYSTEM_SHA256,
        anchors=_EXTRACTION_SYSTEM_ANCHORS,
    )
    assert_that(body).is_equal_to(
        {
            "model": _EXTRACTION_MODEL,
            "max_tokens": 4000,
            "messages": [
                {
                    "role": "user",
                    "content": f"<transcript>\n{_TRANSCRIPT.strip()}\n</transcript>",
                },
            ],
        },
    )


def test_cleanup_sends_messages_request_and_decodes_corrections() -> None:
    """The cleaner's request and response pass through the real SDK."""
    payload = json.dumps(
        {
            "cleaned_text": "a cleaned transcript",
            "corrections": [
                {"original": "alot", "corrected": "a lot", "reason": "spelling"},
            ],
        },
    )
    provider = _Provider(
        lambda _request: httpx2.Response(200, json=_message(payload)),
    )

    result = TranscriptCleaner(client=_client(provider)).cleanup_transcript(
        _TRANSCRIPT,
        podcast_name="Example Show",
        host_name="Host Example",
        guest_name="Guest Example",
        terminology=["podex", "alembic"],
    )

    assert_that(result.errors).is_empty()
    assert_that(result.cleaned_text).is_equal_to("a cleaned transcript")
    assert_that(result.corrections_made[0].corrected).is_equal_to("a lot")
    assert_that(provider.requests).is_length(1)
    _assert_messages_request(provider.requests[0])
    body = provider.bodies()[0]
    _assert_system_prompt(
        body.pop("system", None),
        sha256=_CLEANUP_SYSTEM_SHA256,
        anchors=_CLEANUP_SYSTEM_ANCHORS,
    )
    assert_that(body).is_equal_to(
        {
            "model": _CLEANUP_MODEL,
            "max_tokens": 8000,
            "messages": [{"role": "user", "content": _CLEANUP_USER_MESSAGE}],
        },
    )


@_SERVICES
def test_response_without_text_block_is_empty_not_an_error(
    run_service: Callable[[anthropic.Anthropic], list[str]],
) -> None:
    """Only non-text content blocks leave the service result unchanged."""
    body = {
        **_message("unused"),
        "content": [
            {"type": "thinking", "thinking": "reasoning", "signature": "sig"},
        ],
    }
    provider = _Provider(lambda _request: httpx2.Response(200, json=body))

    errors = run_service(_client(provider))

    assert_that(errors).is_empty()
    assert_that(provider.requests).is_length(1)


@pytest.mark.parametrize(
    ("status", "error_type", "expected"),
    [
        (400, "invalid_request_error", anthropic.BadRequestError),
        (401, "authentication_error", anthropic.AuthenticationError),
        (429, "rate_limit_error", anthropic.RateLimitError),
        (500, "api_error", anthropic.InternalServerError),
        (529, "overloaded_error", anthropic.OverloadedError),
    ],
)
def test_sdk_maps_error_statuses_to_api_status_errors(
    status: int,
    error_type: str,
    expected: type[anthropic.APIStatusError],
    sleeps: list[float],
) -> None:
    """Every provider error the services handle is an ``APIStatusError``."""
    provider = _Provider(_api_error(status, error_type))

    with pytest.raises(anthropic.APIStatusError) as caught:
        _client(provider).messages.create(
            model=_EXTRACTION_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    assert_that(caught.value).is_instance_of(expected)
    assert_that(caught.value.status_code).is_equal_to(status)
    assert_that(len(provider.requests)).is_equal_to(len(sleeps) + 1)


@_SERVICES
def test_rate_limit_is_retried_then_reported(
    run_service: Callable[[anthropic.Anthropic], list[str]],
    sleeps: list[float],
) -> None:
    """A 429 honors ``retry-after`` for each SDK retry, then becomes an error."""
    provider = _Provider(
        lambda _request: httpx2.Response(
            429,
            headers={"retry-after": "7"},
            json={
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "slow down"},
            },
        ),
    )

    errors = run_service(_client(provider))

    assert_that(errors).is_length(1)
    assert_that(errors[0]).ends_with(": 429")
    assert_that(provider.requests).is_length(_SDK_MAX_RETRIES + 1)
    assert_that(
        [request.headers["x-stainless-retry-count"] for request in provider.requests],
    ).is_equal_to([str(retry) for retry in range(_SDK_MAX_RETRIES + 1)])
    assert_that(sleeps).is_equal_to([7.0] * _SDK_MAX_RETRIES)


@_SERVICES
@pytest.mark.parametrize(
    ("status", "error_type"),
    [(500, "api_error"), (529, "overloaded_error")],
)
def test_server_errors_are_retried_then_reported(
    run_service: Callable[[anthropic.Anthropic], list[str]],
    status: int,
    error_type: str,
    sleeps: list[float],
) -> None:
    """Server failures use the SDK back-off and surface as one result error."""
    provider = _Provider(_api_error(status, error_type))

    errors = run_service(_client(provider))

    assert_that(errors).is_equal_to([errors[0]])
    assert_that(errors[0]).ends_with(f": {status}")
    assert_that(provider.requests).is_length(_SDK_MAX_RETRIES + 1)
    assert_that(sleeps).is_length(_SDK_MAX_RETRIES)


@_SERVICES
def test_client_errors_are_reported_without_retry(
    run_service: Callable[[anthropic.Anthropic], list[str]],
    sleeps: list[float],
) -> None:
    """A rejected request is not retried and is reported once."""
    provider = _Provider(_api_error(400, "invalid_request_error"))

    errors = run_service(_client(provider))

    assert_that(errors).is_length(1)
    assert_that(errors[0]).ends_with(": 400")
    assert_that(provider.requests).is_length(1)
    assert_that(sleeps).is_empty()


def _raise_timeout(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ReadTimeout("read timed out", request=request)


def _raise_connect_error(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("connection refused", request=request)


@_SERVICES
@pytest.mark.parametrize(
    "failure",
    [_raise_timeout, _raise_connect_error],
    ids=["timeout", "connection"],
)
def test_transport_failures_are_retried_then_reported(
    run_service: Callable[[anthropic.Anthropic], list[str]],
    failure: Handler,
    sleeps: list[float],
) -> None:
    """Timeouts and connection failures become a network error result."""
    provider = _Provider(failure)

    errors = run_service(_client(provider))

    assert_that(errors).is_length(1)
    assert_that(errors[0]).starts_with("Network error: ")
    assert_that(provider.requests).is_length(_SDK_MAX_RETRIES + 1)
    assert_that(sleeps).is_length(_SDK_MAX_RETRIES)


def test_sdk_maps_read_timeout_to_api_timeout_error(sleeps: list[float]) -> None:
    """Timeouts reach the services as ``APIConnectionError`` subclasses."""
    provider = _Provider(_raise_timeout)

    with pytest.raises(anthropic.APITimeoutError) as caught:
        _client(provider).messages.create(
            model=_EXTRACTION_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    assert_that(caught.value).is_instance_of(anthropic.APIConnectionError)
    assert_that(sleeps).is_length(_SDK_MAX_RETRIES)


@_SERVICES
@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html>bad gateway page</html>",
        ),
        httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            text='{"type": "message", "content": [',
        ),
        httpx2.Response(200, json={"id": "msg_contract", "type": "message"}),
        httpx2.Response(200, json={**_message("[]"), "content": "[]"}),
        httpx2.Response(200, json={**_message("[]"), "content": [{"type": "text"}]}),
    ],
    ids=[
        "html-body",
        "truncated-json",
        "missing-content",
        "content-not-a-list",
        "text-block-without-text",
    ],
)
def test_malformed_success_responses_are_reported(
    run_service: Callable[[anthropic.Anthropic], list[str]],
    response: httpx2.Response,
    sleeps: list[float],
) -> None:
    """A 200 that is not a Messages API body is an error, not a crash."""
    provider = _Provider(
        lambda request: httpx2.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        ),
    )

    errors = run_service(_client(provider))

    assert_that(errors).is_length(1)
    assert_that(errors[0]).starts_with("Malformed API response: ")
    assert_that(provider.requests).is_length(1)
    assert_that(sleeps).is_empty()


def test_services_build_default_sdk_client_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an injected client the services build the SDK default client."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", _API_KEY)

    extractor, cleaner = LLMExtractor(), TranscriptCleaner()

    assert_that(extractor.model).is_equal_to(_EXTRACTION_MODEL)
    assert_that(cleaner.model).is_equal_to(_CLEANUP_MODEL)
    for client in (extractor.client, cleaner.client):
        assert_that(client).is_instance_of(anthropic.Anthropic)
        assert_that(str(client.base_url)).is_equal_to("https://api.anthropic.com")
        assert_that(client.max_retries).is_equal_to(_SDK_MAX_RETRIES)


def test_network_guard_blocks_a_real_sdk_request(
    network_attempts: list[object],
) -> None:
    """The module guard stops the SDK's default transport and datagram sends."""
    client = anthropic.Anthropic(api_key=_API_KEY, max_retries=0)

    with pytest.raises(anthropic.APIConnectionError) as caught:
        client.messages.create(
            model=_EXTRACTION_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    assert_that(network_attempts).is_length(1)
    assert_that(str(caught.value.__cause__)).contains("network access is blocked")

    loopback = ("127.0.0.1", 9)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        with pytest.raises(ConnectionRefusedError):
            udp.sendto(b"ping", loopback)
        with pytest.raises(ConnectionRefusedError):
            udp.sendmsg([b"ping"], [], 0, loopback)

    assert_that(network_attempts).is_length(3)
    network_attempts.clear()
