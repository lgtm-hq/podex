"""Third-party clients log no credentials, bodies or error text at DEBUG.

Each test configures logging as the application does, enables DEBUG, and
drives a real client over a fake transport: the LLM SDK and its HTTP client
over an in-memory transport, the HTTP clients' connection layers over an
in-memory network backend, the requests stack over a socket pair, the
object storage client over a send hook, and the ASGI server over an
in-memory asyncio transport. Synthetic secrets are planted in request
headers, URL userinfo, credential query fields, response headers, bodies
and error text; the captured output (formatted text, record messages,
arguments and tracebacks) is searched for each, and the method, host and
status must still be there. Nothing reaches the network.
"""

import asyncio
import json
import socket
from collections.abc import Callable, Iterator
from typing import Any, cast

import anthropic
import boto3
import httpcore
import httpcore2
import httpx
import httpx2
import pytest
import requests
import urllib3
from assertpy import assert_that
from botocore.awsrequest import AWSResponse
from uvicorn.config import Config
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState

from podex.redaction import REDACTED
from tests.sdk_log_support import (
    CREDENTIAL_QUERY_KEYS,
    MemoryTransport,
    debug_logging,
    logged,
    synthetic_secret,
)

_LLM_HOST = "llm.example.test"
_API_HOST = "api.example.test"
_STORAGE_HOST = "storage.example.test"


@pytest.fixture(name="secrets")
def secrets_fixture() -> dict[str, str]:
    """Return one synthetic secret per place a client could log one."""
    return {
        purpose: synthetic_secret(purpose)
        for purpose in (
            "api-key",
            "request-header",
            "userinfo",
            "query",
            "request-body",
            "response-header",
            "response-body",
            "error",
            "configured",
        )
    }


@pytest.fixture(name="sdk_logs")
def sdk_logs_fixture(
    caplog: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> Iterator[pytest.LogCaptureFixture]:
    """Configure logging at DEBUG with the configured secret registered.

    Yields:
        pytest.LogCaptureFixture: The capture fixture.
    """
    with debug_logging(caplog, (secrets["configured"],)):
        yield caplog


def _assert_clean(output: str, secrets: dict[str, str]) -> None:
    """Assert that no planted secret appears in ``output``."""
    for purpose, value in secrets.items():
        assert_that(output).described_as(purpose).does_not_contain(value)


# --- LLM SDK (anthropic, httpx2) ---------------------------------------------


def _llm_client(
    secrets: dict[str, str],
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> anthropic.Anthropic:
    """Return a real LLM SDK client whose HTTP client answers in memory."""
    return anthropic.Anthropic(
        api_key=secrets["api-key"],
        base_url=f"https://reader:{secrets['userinfo']}@{_LLM_HOST}",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _ask(client: anthropic.Anthropic, secrets: dict[str, str]) -> object:
    """Send one message request carrying the request-side secrets."""
    reply: object = client.messages.create(
        model="claude-test",
        max_tokens=16,
        messages=[{"role": "user", "content": f"prompt {secrets['request-body']}"}],
        extra_headers={"Authorization": f"Bearer {secrets['request-header']}"},
        extra_query={"token": secrets["query"]},
    )
    return reply


def _llm_response(status: int, secrets: dict[str, str]) -> httpx2.Response:
    """Return a provider response with secrets in headers and body."""
    headers = {
        "x-provider-trace": secrets["response-header"],
        "set-cookie": f"session={secrets['response-header']}",
        "server": secrets["configured"],
        "request-id": "req_test",
    }
    if status >= 400:
        body: dict[str, Any] = {
            "type": "error",
            "error": {"type": "api_error", "message": secrets["response-body"]},
        }
    else:
        body = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": secrets["response-body"]}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    return httpx2.Response(status, headers=headers, json=body)


def test_llm_success_logs_no_secret(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A successful request keeps method, host, path and status only."""
    client = _llm_client(secrets, lambda _request: _llm_response(200, secrets))

    _ask(client, secrets)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f"HTTP Request: POST https://{_LLM_HOST}/v1/messages"
        '?token=[redacted] "HTTP/1.1 200 OK"',
        "Request options: method=POST url=/v1/messages "
        "params={'token': '[redacted]'} headers={} (+1 other headers) "
        "(+8 other options)",
        "'request-id': 'req_test'",
        "(+2 other headers)",
    )
    assert_that(output).does_not_contain("x-provider-trace", "set-cookie")


def test_llm_error_status_logs_no_secret(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A failed request keeps the status and the masked header names."""
    client = _llm_client(secrets, lambda _request: _llm_response(500, secrets))

    with pytest.raises(anthropic.InternalServerError):
        _ask(client, secrets)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f"HTTP Response: POST https://{_LLM_HOST}/v1/messages"
        '?token=[redacted] "500 Internal Server Error"',
        "'server': '[redacted]'",
        "(+2 other headers)",
    )


def test_llm_connection_error_logs_class_not_text(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A transport failure is logged with its class and frames, not its text."""

    def fail(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(
            f"connect to {request.url} failed: {secrets['error']}",
            request=request,
        )

    with pytest.raises(anthropic.APIConnectionError):
        _ask(_llm_client(secrets, fail), secrets)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "Encountered Exception",
        "Traceback (most recent call last):",
        "httpx2.ConnectError: [message withheld]",
        f"Sending HTTP Request: POST https://{_LLM_HOST}/v1/messages",
    )
    assert_that(
        [record.exc_info for record in sdk_logs.records if record.exc_info]
    ).is_empty()


# --- HTTP client request lines (httpx, httpx2) ---------------------------------


@pytest.mark.parametrize("client_module", [httpx, httpx2], ids=["httpx", "httpx2"])
@pytest.mark.parametrize("key", CREDENTIAL_QUERY_KEYS)
def test_request_line_masks_credential_query_values(
    client_module: Any,
    key: str,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The INFO request line masks userinfo and each credential query value."""
    url = (
        f"https://reader:{secrets['userinfo']}@{_API_HOST}/v1/items"
        f"?{key}={secrets['query']}&page=2"
    )
    transport = client_module.MockTransport(
        lambda request: client_module.Response(200, request=request),
    )
    with client_module.Client(transport=transport) as client:
        client.get(url)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f"HTTP Request: GET https://{_API_HOST}/v1/items"
        f'?{key}=[redacted]&page=2 "HTTP/1.1 200 OK"',
    )


# --- HTTP connection layers (httpcore, httpcore2) -------------------------------


def _wire_response(secrets: dict[str, str]) -> bytes:
    """Return an HTTP/1.1 response with secrets in headers and body."""
    body = secrets["response-body"].encode()
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain\r\n"
        b"Set-Cookie: session=" + secrets["response-header"].encode() + b"\r\n"
        b"X-Provider-Trace: " + secrets["response-header"].encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )


_CONNECTION_LAYERS = [
    pytest.param((httpx, httpcore, "httpcore._sync.connection_pool"), id="httpcore"),
    pytest.param(
        (httpx2, httpcore2, "httpcore2._sync.connection_pool"),
        id="httpcore2",
    ),
]


def _answer_on_wire(
    monkeypatch: pytest.MonkeyPatch,
    layer: tuple[Any, Any, str],
    wire: bytes,
) -> Any:
    """Make the layer's connection pools read ``wire``; return the client module."""
    client_module, core, pool_module = layer
    monkeypatch.setattr(f"{pool_module}.SyncBackend", lambda: core.MockBackend([wire]))
    return client_module


@pytest.mark.parametrize("layer", _CONNECTION_LAYERS)
def test_connection_layer_masks_response_headers(
    layer: tuple[Any, Any, str],
    monkeypatch: pytest.MonkeyPatch,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """Wire response headers outside the allowlist are masked."""
    client_module = _answer_on_wire(monkeypatch, layer, _wire_response(secrets))
    url = f"http://reader:{secrets['userinfo']}@{_API_HOST}/v1/items"
    with client_module.Client() as client:
        response = client.get(
            url,
            params={"token": secrets["query"]},
            headers={"Authorization": f"Bearer {secrets['request-header']}"},
        )

    output = logged(sdk_logs)
    assert_that(response.status_code).is_equal_to(200)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "receive_response_headers.complete protocol=HTTP/1.1 status=200",
        f"connect_tcp.started host='{_API_HOST}' port=80",
    )


@pytest.mark.parametrize("layer", _CONNECTION_LAYERS)
def test_connection_layer_failure_withholds_error_text(
    layer: tuple[Any, Any, str],
    monkeypatch: pytest.MonkeyPatch,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A protocol error quoting the response is logged by class only."""
    wire = f"NOT-HTTP {secrets['error']}\r\n\r\n".encode()
    client_module = _answer_on_wire(monkeypatch, layer, wire)
    with client_module.Client() as client, pytest.raises(client_module.HTTPError):
        client.get(f"http://{_API_HOST}/v1/items")

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "receive_response_headers.failed exception=RemoteProtocolError",
    )


# --- requests and urllib3 (the Spotify SDK's HTTP stack) ------------------------


def _socket_connection(wire: bytes) -> Callable[[object], socket.socket]:
    """Return a connection factory answering every request with ``wire``."""
    peers: list[socket.socket] = []

    def connect(_connection: object) -> socket.socket:
        ours, theirs = socket.socketpair()
        theirs.sendall(wire)
        peers.append(theirs)
        return ours

    return connect


def test_requests_stack_masks_request_line(
    monkeypatch: pytest.MonkeyPatch,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The connection pool's request line masks credential query values."""
    monkeypatch.setattr(
        "urllib3.connection.HTTPConnection._new_conn",
        _socket_connection(_wire_response(secrets)),
    )
    with requests.Session() as session:
        response = session.get(
            f"http://reader:{secrets['userinfo']}@{_API_HOST}/v1/items",
            params={"access_token": secrets["query"], "page": "2"},
            headers={"Authorization": f"Bearer {secrets['request-header']}"},
            timeout=5,
        )

    output = logged(sdk_logs)
    assert_that(response.status_code).is_equal_to(200)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f"Starting new HTTP connection (1): {_API_HOST}:80",
        f'http://{_API_HOST}:80 "GET /v1/items?access_token=[redacted]&page=2 '
        'HTTP/1.1" 200',
    )


def test_requests_stack_failure_withholds_error_text(
    monkeypatch: pytest.MonkeyPatch,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A retried connection failure is logged by class, with a masked URL."""

    def refuse(connection: urllib3.connection.HTTPConnection) -> socket.socket:
        raise urllib3.exceptions.NewConnectionError(
            connection,
            f"refused: {secrets['error']}",
        )

    monkeypatch.setattr("urllib3.connection.HTTPConnection._new_conn", refuse)
    with requests.Session() as session:
        session.mount("http://", requests.adapters.HTTPAdapter(max_retries=1))
        with pytest.raises(requests.ConnectionError):
            session.get(
                f"http://{_API_HOST}/v1/items",
                params={"token": secrets["query"]},
                timeout=5,
            )

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "Retrying (Retry(total=0)) after connection broken by "
        "'urllib3.exceptions.NewConnectionError': /v1/items?token=[redacted]",
    )


# --- Object storage client (boto3, botocore) ------------------------------------


class _RawBody:
    """The part of a raw HTTP response body the storage client reads."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, **_kwargs: object) -> Iterator[bytes]:
        """Yield the whole body."""
        yield self._body

    def read(self, *_args: object, **_kwargs: object) -> bytes:
        """Return the whole body."""
        return self._body


def _storage_client(secrets: dict[str, str]) -> Any:
    """Return a real storage client with synthetic credentials."""
    client: Any = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=f"https://{_STORAGE_HOST}",
        aws_access_key_id=f"AKIA{secrets['api-key']}",
        aws_secret_access_key=secrets["configured"],
        aws_session_token=secrets["request-header"],
    )
    return client


@pytest.mark.parametrize("status", [200, 403])
def test_storage_client_logs_no_secret(
    status: int,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """Signing, request and response lines keep method, URL and status only."""
    client = _storage_client(secrets)

    def answer(request: Any, **_kwargs: object) -> AWSResponse:
        body = b""
        if status >= 400:
            body = (
                f"<Error><Code>AccessDenied</Code><Message>{secrets['response-body']}"
                "</Message></Error>"
            ).encode()
        headers = {
            "x-amz-request-id": "req-test",
            "x-provider-trace": secrets["response-header"],
        }
        response: AWSResponse = AWSResponse(
            request.url, status, headers, _RawBody(body)
        )
        return response

    client.meta.events.register("before-send.s3.PutObject", answer)
    try:
        client.put_object(
            Bucket="artifacts",
            Key="transcripts/1.json",
            Body=secrets["request-body"].encode(),
        )
    except client.exceptions.ClientError:
        assert_that(status).is_equal_to(403)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "Sending http request: <botocore.awsrequest.AWSPreparedRequest "
        f"method=PUT url=https://{_STORAGE_HOST}/artifacts/transcripts/1.json ",
        "CanonicalRequest:\nPUT /artifacts/transcripts/1.json headers=",
        "Signature:\n[redacted]",
        "Response headers: {'x-amz-request-id': 'req-test'} (+1 other headers)",
        "Response body:\n[withheld response body (",
    )
    assert_that(output).does_not_contain(
        "authorization",
        "x-amz-security-token",
        "x-provider-trace",
    )
    assert_that(output).does_not_contain("AccessDenied", "error code")


def test_storage_client_failure_withholds_error_text(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A send failure is logged with its class and frames, not its text."""
    client = _storage_client(secrets)

    def fail(**_kwargs: object) -> AWSResponse:
        raise ConnectionError(f"reset by peer: {secrets['error']}")

    client.meta.events.register("before-send.s3.PutObject", fail)
    with pytest.raises(ConnectionError):
        client.put_object(Bucket="artifacts", Key="k", Body=b"x")

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "Exception received when sending HTTP request.",
        "Traceback (most recent call last):",
        "ConnectionError: [message withheld]",
    )


# --- ASGI server access log (uvicorn) -------------------------------------------


async def _serve_once(request: bytes) -> bytes:
    """Serve one request through the server's HTTP/1.1 protocol in memory."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        del scope, receive
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    config = Config(app=app, access_log=True, log_config=None)
    config.load()
    protocol = H11Protocol(config=config, server_state=ServerState(), app_state={})
    transport = MemoryTransport()
    protocol.connection_made(cast(asyncio.Transport, transport))
    protocol.data_received(request)
    for _ in range(100):
        if b"\r\n\r\n" in transport.written:
            break
        await asyncio.sleep(0)
    return bytes(transport.written)


def test_server_access_log_masks_credential_query_values(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The access line keeps client, method, path and status, not credentials."""
    request = (
        f"GET /api/v2/podcasts?code={secrets['query']}&page=2 HTTP/1.1\r\n"
        f"Host: {_API_HOST}\r\n"
        f"Authorization: Bearer {secrets['request-header']}\r\n\r\n"
    ).encode()

    written = asyncio.run(_serve_once(request))

    output = logged(sdk_logs)
    assert_that(written.decode()).starts_with("HTTP/1.1 204")
    _assert_clean(output, secrets)
    assert_that(output).contains(
        '127.0.0.1:5000 - "GET /api/v2/podcasts?code=[redacted]&page=2 HTTP/1.1" 204',
    )


def test_request_options_body_is_withheld_not_dropped(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The LLM SDK's request options keep their shape with the body withheld."""
    seen: list[dict[str, Any]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return _llm_response(200, secrets)

    _ask(_llm_client(secrets, answer), secrets)

    output = logged(sdk_logs)
    assert_that(seen[0]["messages"][0]["content"]).contains(secrets["request-body"])
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "Request options: method=POST url=/v1/messages "
        "params={'token': '[redacted]'}",
    )


# --- Record forms found in review of the boundary --------------------------------


def _records(caplog: pytest.LogCaptureFixture, start: str) -> list[Any]:
    """Return the captured records whose message starts with ``start``."""
    return [record for record in caplog.records if str(record.msg).startswith(start)]


def test_llm_request_options_mask_mixed_case_credential_names(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """Credential names in any case or spelling are masked in structured args."""
    client = _llm_client(secrets, lambda _request: _llm_response(200, secrets))

    client.messages.create(
        model="claude-test",
        max_tokens=16,
        messages=[{"role": "user", "content": "hello"}],
        extra_query={"ToKeN": secrets["query"], "AWSAccessKeyId": secrets["api-key"]},
    )

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    options = _records(sdk_logs, "Request options")
    assert_that(options).is_not_empty()
    assert_that(options[0].getMessage()).contains(
        f"params={{'ToKeN': '{REDACTED}', 'AWSAccessKeyId': '{REDACTED}'}}",
    )


def test_llm_standalone_header_records_follow_the_header_allowlist(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The SDK's one-header records keep the request id and mask the rest."""

    def answer(_request: httpx2.Request) -> httpx2.Response:
        response = _llm_response(200, secrets)
        response.headers["anthropic-workspace-id"] = secrets["response-header"]
        return response

    _ask(_llm_client(secrets, answer), secrets)

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains("request_id: req_test", "workspace_id: [withheld]")
    assert_that(_records(sdk_logs, "workspace_id")[0].args).is_equal_to(
        ("[withheld]",),
    )


@pytest.mark.parametrize("client_module", [httpx, httpx2], ids=["httpx", "httpx2"])
def test_request_line_replaces_provider_reason_phrase(
    client_module: Any,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The request line prints the standard phrase, not the provider's."""
    reason = f"Internal {secrets['response-body']}".encode()
    transport = client_module.MockTransport(
        lambda request: client_module.Response(
            500,
            request=request,
            extensions={"reason_phrase": reason},
        ),
    )
    with client_module.Client(transport=transport) as client:
        response = client.get(f"https://{_API_HOST}/v1/items")

    output = logged(sdk_logs)
    assert_that(response.reason_phrase).contains(secrets["response-body"])
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f'HTTP Request: GET https://{_API_HOST}/v1/items "HTTP/1.1 500 '
        'Internal Server Error"',
    )
    line = _records(sdk_logs, "HTTP Request")[0]
    assert_that(line.args[3:]).is_equal_to((500, "Internal Server Error"))


def test_connection_pool_decodes_query_names(
    monkeypatch: pytest.MonkeyPatch,
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """A percent-encoded credential query name is recognised in a request line."""
    monkeypatch.setattr(
        "urllib3.connection.HTTPConnection._new_conn",
        _socket_connection(_wire_response(secrets)),
    )
    with urllib3.PoolManager() as pool:
        response = pool.request(
            "GET",
            f"http://{_API_HOST}/v1/items?api%5Fkey={secrets['query']}&page=2",
        )

    output = logged(sdk_logs)
    assert_that(response.status).is_equal_to(200)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        f'http://{_API_HOST}:80 "GET /v1/items?api_key=[redacted]&page=2 HTTP/1.1"'
        " 200",
    )


def test_server_access_log_decodes_query_names(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """The access line masks ``%63ode`` and keeps its five arguments."""
    request = (
        f"GET /auth/callback?%63ode={secrets['query']}&state=x HTTP/1.1\r\n"
        f"Host: {_API_HOST}\r\n\r\n"
    ).encode()

    asyncio.run(_serve_once(request))

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    access = [r for r in sdk_logs.records if r.name == "uvicorn.access"]
    assert_that(access).is_length(1)
    assert_that(access[0].args).is_equal_to(
        (
            "127.0.0.1:5000",
            "GET",
            "/auth/callback?code=[redacted]&state=x",
            "1.1",
            204,
        ),
    )


def test_storage_signing_dump_follows_the_header_allowlist(
    sdk_logs: pytest.LogCaptureFixture,
    secrets: dict[str, str],
) -> None:
    """Canonical request header lines keep only allowlisted values."""
    client = _storage_client(secrets)

    def answer(request: Any, **_kwargs: object) -> AWSResponse:
        response: AWSResponse = AWSResponse(request.url, 200, {}, _RawBody(b""))
        return response

    client.meta.events.register("before-send.s3.PutObject", answer)
    client.put_object(
        Bucket="artifacts",
        Key="k",
        Body=b"x",
        Metadata={"private": secrets["request-body"]},
    )

    output = logged(sdk_logs)
    _assert_clean(output, secrets)
    assert_that(output).contains(
        "CanonicalRequest:\nPUT /artifacts/k headers=",
        f"'host': '{_STORAGE_HOST}'",
        "signed_headers=",
    )
    assert_that(output).does_not_contain("x-amz-meta-private", "x-amz-security-token")
