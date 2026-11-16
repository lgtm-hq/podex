"""Known forms validate shape and parts strictly, cheaply and within bounds.

Each test configures logging as the application does and searches the
formatted output and the raw records (``msg``, ``args``, ``exc_text``,
``exc_info``, ``stack_info``). Real library code runs where it can (the
ASGI server's WebSocket protocol over an in-memory transport); otherwise the
exact record form a library logs is used.
"""

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

import pytest
from assertpy import assert_that
from uvicorn.config import Config
from uvicorn.protocols.websockets.websockets_sansio_impl import (
    WebSocketsSansIOProtocol,
)
from uvicorn.server import ServerState

from tests.sdk_log_support import (
    MemoryTransport,
    debug_logging,
    logged,
    synthetic_secret,
)

_WITHHELD = "[record withheld: unrecognised form"
_NONCE = base64.b64encode(b"sixteen byte key").decode()
_MEGABYTE = 1_000_000


def _everything(caplog: pytest.LogCaptureFixture) -> str:
    """Return everything the records could print, stack text included."""
    stacks = [str(record.stack_info) for record in caplog.records]
    return "\n".join([logged(caplog), *stacks])


@pytest.fixture(name="secret")
def secret_fixture() -> str:
    """Return a fresh synthetic secret."""
    return synthetic_secret("strict")


async def _reject(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Refuse every WebSocket handshake."""
    del scope
    await receive()
    await send({"type": "websocket.close"})


async def _handshake(request: bytes) -> bytes:
    """Run one WebSocket handshake through the server's protocol in memory."""
    config = Config(app=_reject, ws="websockets-sansio", log_config=None)
    config.load()
    protocol = WebSocketsSansIOProtocol(
        config=config,
        server_state=ServerState(),
        app_state={},
    )
    transport = MemoryTransport()
    protocol.connection_made(cast(asyncio.Transport, transport))
    protocol.data_received(request)
    for _ in range(100):
        if transport.written:
            break
        await asyncio.sleep(0)
    return bytes(transport.written)


def test_websocket_handshake_records_print_no_request_material(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The server's WebSocket lines keep target shape and header names only."""
    request = (
        f"GET /auth/callback?code={secret}&state=x HTTP/1.1\r\n"
        "Host: api.example.test\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {_NONCE}\r\nSec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {secret}\r\nCookie: session={secret}\r\n\r\n"
    ).encode()

    with debug_logging(caplog):
        written = asyncio.run(_handshake(request))

    server = [r for r in caplog.records if r.name == "uvicorn.error"]
    messages = [record.getMessage() for record in server]
    assert_that(written.decode()).starts_with("HTTP/1.1 403")
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(messages).contains(
        "< GET /auth/callback?code=[redacted]&state=x HTTP/1.1",
        "< host: [withheld]",
        "< [withheld]: [withheld]",
        '127.0.0.1:5000 - "WebSocket /auth/callback?code=[redacted]&state=x" 403',
        "> HTTP/1.1 403 Forbidden",
    )
    rejected = [r for r in server if str(r.msg).startswith("connection rejected")]
    assert_that(rejected).is_not_empty()
    assert_that(rejected[0].args).is_equal_to((403, "Forbidden"))


def test_other_server_records_are_left_as_they_are(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Start-up and other server lines keep their message and arguments."""
    with debug_logging(caplog):
        logging.getLogger("uvicorn.error").info(
            "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)",
            "http",
            "127.0.0.1",
            8000,
        )

    record = caplog.records[-1]
    assert_that(record.args).is_equal_to(("http", "127.0.0.1", 8000))


def test_spotify_request_line_prints_method_and_url_only(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Body text shaped like headers is never promoted into the skeleton."""
    line = (
        "Sending POST to https://api.spotify.com/v1/playlists/x/tracks with Params: "
        "{} Headers: {'Authorization': 'Bearer t1'} and Body:  Headers: "
        f'{{"Content-Type": "{secret}"}} and Body: ignored'
    )
    with debug_logging(caplog):
        logging.getLogger("spotipy.client").debug(line)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        "Sending POST to https://api.spotify.com/v1/playlists/x/tracks",
    )
    assert_that(_everything(caplog)).does_not_contain(secret, "t1")


@pytest.mark.parametrize(
    ("logger_name", "head"),
    [
        ("spotipy.client", "Sending GET to "),
        ("spotipy.client", "HTTP Error for GET to x with Params: "),
        ("spotipy.oauth2", "Sending POST request to "),
        (
            "spotipy.util",
            "Your application has reached a rate/request limit. "
            "Retry will occur after: ",
        ),
        ("httpcore.http11", "receive_response_headers.complete "),
        ("httpcore2.connection", "connect_tcp.started host='"),
        ("sentry_sdk.envelopes", "HTTP 413 "),
        ("spotipy.client", "RESULTS: "),
    ],
)
def test_preformatted_lines_are_bounded_on_adversarial_input(
    logger_name: str,
    head: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A one-megabyte line that starts like a known form is handled quickly."""
    line = head + (" returned 200 due to a" * (_MEGABYTE // 21))
    log = logging.getLogger(logger_name)
    with debug_logging(caplog):
        log.debug(line)
        started = time.perf_counter()
        log.debug(line)
        elapsed = time.perf_counter() - started

    message = caplog.records[-1].getMessage()
    assert_that(elapsed).is_less_than(0.05)
    assert_that(len(message)).is_less_than(512)
    assert_that(message).does_not_contain("due to a")


def test_spotify_request_line_near_the_old_parse_bound_is_quick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 64,847-character request line with repeated header markers is quick."""
    line = "Sending GET to https://api.spotify.com/v1/x with Params: {}"
    line += " Headers: {}" * ((64_847 - len(line)) // 12)
    log = logging.getLogger("spotipy.client")
    with debug_logging(caplog):
        log.debug(line)
        started = time.perf_counter()
        log.debug(line)
        elapsed = time.perf_counter() - started

    assert_that(elapsed).is_less_than(0.05)
    assert_that(caplog.records[-1].getMessage()).starts_with(_WITHHELD)


def test_storage_response_body_reads_nothing_from_the_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Code-shaped text inside the body is not printed."""
    body = (
        b"<Message><![CDATA[<Code>privatevalue</Code>]]></Message>"
        b"<Code>AccessDenied</Code>"
    )
    with debug_logging(caplog):
        logging.getLogger("botocore.parsers").debug("Response body:\n%r", body)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        f"Response body:\n[withheld response body ({len(body)} bytes)]",
    )
    assert_that(_everything(caplog)).does_not_contain("privatevalue", "AccessDenied")


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "connect_tcp.started host='a.test' port=443",
            "connect_tcp.started host='a.test' port=443",
        ),
        ("close.complete", "close.complete"),
        ("k1_private_step.started", f"{_WITHHELD}, 23 characters]"),
        ("connect_tcp.k1done", f"{_WITHHELD}, 18 characters]"),
    ],
)
def test_trace_steps_are_the_enumerated_ones(
    message: str,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the steps and outcomes the transports emit are printed."""
    with debug_logging(caplog):
        logging.getLogger("httpcore.http11").debug(message)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(expected)
    assert_that(_everything(caplog)).does_not_contain("k1")


class _LoudInt(int):
    """An integer that renders as text of its choosing."""

    def __str__(self) -> str:
        return "loud-k1"

    def __repr__(self) -> str:
        return "loud-k1"

    def __format__(self, spec: str) -> str:
        return "loud-k1"


class _LoudStr(str):
    """A string that renders as text of its choosing."""

    __slots__ = ()

    def __str__(self) -> str:
        return "loud-k1"


@dataclass
class _Loudk1Body:
    """A body of a type whose name carries text."""

    note: str = ""


@pytest.mark.parametrize(
    ("message", "arguments", "expected"),
    [
        ("%i retries left", (_LoudInt(2),), "[withheld] retries left"),
        (
            "Retrying request to %s in %f seconds",
            (_LoudStr("/v1/x"), 0.5),
            "Retrying request to [withheld] in 0.500000 seconds",
        ),
        ("request_id: %s", (_LoudStr("req_1"),), "request_id: [withheld]"),
    ],
)
def test_validated_parts_are_builtin_values(
    message: str,
    arguments: tuple[object, ...],
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Subclasses of numbers and strings never print their own text."""
    with debug_logging(caplog):
        logging.getLogger("anthropic._base_client").debug(message, *arguments)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(expected)
    assert_that(_everything(caplog)).does_not_contain("k1")


def test_body_of_an_unknown_type_is_described_by_a_constant(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body's type name is not printed."""
    with debug_logging(caplog):
        logging.getLogger("botocore.parsers").debug("Response body:\n%r", _Loudk1Body())

    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        "Response body:\n[withheld response body]",
    )


_HUGE = {f"k{index}": "v" for index in range(1_000_000)}


@pytest.mark.parametrize(
    ("logger_name", "message", "arguments"),
    [
        pytest.param("urllib3.connectionpool", "Retry: %s", (_HUGE,) * 8, id="arity"),
        pytest.param(
            "botocore.parsers",
            "Response headers: %r",
            (_HUGE,) * 8,
            id="arity-headers",
        ),
        pytest.param("urllib3.connectionpool", "Retry: %s", (_HUGE,), id="url-mapping"),
        pytest.param(
            "anthropic._base_client",
            "Request options: %s",
            (_HUGE,),
            id="million-options",
        ),
        pytest.param(
            "anthropic._base_client",
            "Request options: %s",
            ({"method": "post", "params": _HUGE, "headers": _HUGE},),
            id="million-params",
        ),
    ],
)
def test_shape_is_checked_before_any_work(
    logger_name: str,
    message: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Wrong arity, wrong types and huge mappings cost almost nothing."""
    log = logging.getLogger(logger_name)
    with debug_logging(caplog):
        log.debug(message, *arguments)
        started = time.perf_counter()
        log.debug(message, *arguments)
        elapsed = time.perf_counter() - started

    assert_that(elapsed).is_less_than(0.05)
    assert_that(len(caplog.records[-1].getMessage())).is_less_than(2_000)


def _raise_wide_group() -> None:
    """Raise an exception group with a thousand members."""
    raise ExceptionGroup(
        "many", [ValueError(f"member {index}") for index in range(1_000)]
    )


def test_rebuilt_traceback_is_bounded(caplog: pytest.LogCaptureFixture) -> None:
    """A thousand-member group prints a bounded traceback with counts."""
    with debug_logging(caplog):
        try:
            _raise_wide_group()
        except ExceptionGroup:
            logging.getLogger("anthropic._base_client").debug(
                "Encountered Exception",
                exc_info=True,
            )

    text = str(caplog.records[-1].exc_text)
    assert_that(len(text)).is_less_than_or_equal_to(16_384)
    assert_that(text).contains(
        "ExceptionGroup: [message withheld] (1000 sub-exceptions)",
        "+---------------- +984 more members",
    )
    assert_that(text).does_not_contain("member 1")


@pytest.mark.parametrize(
    ("logger_name", "call", "expected"),
    [
        ("psycopg", (logging.WARNING, "error ignored terminating %r: %s"), None),
        ("psycopg.pool", (logging.WARNING, "connection attempt: %s"), None),
        ("redis.connection", (logging.DEBUG, "Operation failed: %s"), None),
        ("push_response", (logging.INFO, "push message: %s"), None),
        ("opentelemetry.trace", (logging.WARNING, "invalid attribute %s"), None),
        (
            "opentelemetry.trace",
            (logging.WARNING, "Overriding of current TracerProvider is not allowed"),
            "Overriding of current TracerProvider is not allowed",
        ),
        (
            "psycopg",
            (logging.WARNING, "unknown PostgreSQL timezone: %r; will use UTC"),
            "unknown PostgreSQL timezone: [withheld]; will use UTC",
        ),
        ("uvicorn.asgi", (5, "%s - ASGI [%d] Send %s"), None),
    ],
)
def test_more_libraries_are_allowlisted_by_form(
    logger_name: str,
    call: tuple[int, str],
    expected: str | None,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The database driver, cache client and telemetry API follow the rule."""
    level, message = call
    arity = message.count("%") - 2 * message.count("%%")
    arguments = tuple(f"value {secret}" for _ in range(arity))
    with debug_logging(caplog):
        caplog.set_level(1)
        logging.getLogger(logger_name).log(level, message, *arguments)

    record = caplog.records[-1]
    if expected is None:
        expected = f"{_WITHHELD}, {len(message)} characters]"
    assert_that(record.getMessage()).is_equal_to(expected)
    assert_that(_everything(caplog)).does_not_contain(secret)
