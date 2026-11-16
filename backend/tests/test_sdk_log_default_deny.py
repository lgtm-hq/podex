"""Third-party records are allowlisted by form; the rest is withheld.

Each test configures logging as the application does and searches the
formatted output and the raw records (``msg``, ``args``, ``exc_text``,
``exc_info``, ``stack_info``). Real clients run over fake transports or
stubs; where a test logs an SDK record directly, it uses the exact form the
SDK logs.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import anthropic
import httpcore
import httpcore2
import httpx
import httpx2
import pytest
from assertpy import assert_that
from botocore.awsrequest import AWSResponse
from botocore.exceptions import MetadataRetrievalError
from botocore.utils import ContainerMetadataFetcher

from tests.sdk_log_support import (
    assert_quick_record,
    cached_record_text,
    debug_logging,
    logged,
    synthetic_secret,
)

_LLM_HOST = "llm.example.test"
_SOURCE_MARKER = "literal-source-secret-0821"
_WITHHELD = "[record withheld: unrecognised form"


def _everything(caplog: pytest.LogCaptureFixture) -> str:
    """Return everything the records could print, stack text included."""
    stacks = [str(record.stack_info) for record in caplog.records]
    return "\n".join([logged(caplog), *stacks])


@pytest.fixture(name="secret")
def secret_fixture() -> str:
    """Return a fresh synthetic secret."""
    return synthetic_secret("deny")


@pytest.mark.parametrize(
    "logger_name",
    [
        "anthropic._base_client",
        "httpx",
        "httpcore.http11",
        "urllib3.connectionpool",
        "spotipy.client",
        "botocore.utils",
        "s3transfer.tasks",
        "charset_normalizer",
        "bs4.dammit",
    ],
)
@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
def test_unrecognised_records_are_withheld(
    logger_name: str,
    level: int,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown record keeps logger, level, size and exception classes."""
    with debug_logging(caplog):
        try:
            raise ValueError(secret)
        except ValueError:
            logging.getLogger(logger_name).log(
                level,
                "detail: %s",
                {"note": secret},
                exc_info=True,
                stack_info=True,
            )

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.name).is_equal_to(logger_name)
    assert_that(record.levelno).is_equal_to(level)
    assert_that(record.msg).is_equal_to(
        f"{_WITHHELD}, 10 characters; exception ValueError]",
    )
    assert_that(record.args).is_empty()
    assert_that(record.exc_info).is_none()
    assert_that(record.exc_text).is_none()
    assert_that(record.stack_info).is_none()


@dataclass
class _MetadataSession:
    """A session answering the container metadata endpoint from memory."""

    body: bytes

    def send(self, request: Any) -> AWSResponse:
        """Answer 200 with a body that is not JSON."""
        response: AWSResponse = AWSResponse(request.url, 200, {}, _Raw(self.body))
        return response


@dataclass
class _Raw:
    """The part of a raw response body the storage client reads."""

    body: bytes

    def stream(self, **_kwargs: object) -> Iterator[bytes]:
        """Yield the whole body."""
        yield self.body


def test_container_metadata_body_is_withheld(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The metadata service's unparsable body never reaches the logs."""
    body = (
        f'{{"AccessKeyId": "AKIA{secret}", "SecretAccessKey": "{secret}-s", '
        f'"Token": "{secret}-t"'
    ).encode()
    fetcher = ContainerMetadataFetcher(
        session=_MetadataSession(body),
        sleep=lambda _seconds: None,
    )
    with debug_logging(caplog), pytest.raises(MetadataRetrievalError):
        fetcher.retrieve_full_uri("http://169.254.170.2/v2/credentials/x")

    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that([record.getMessage() for record in caplog.records]).contains(
        f"{_WITHHELD}, 5 characters]",
    )


def test_llm_response_record_prints_the_standard_reason(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The LLM SDK's DEBUG response line drops the provider's reason phrase."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            500,
            request=request,
            json={"type": "error", "error": {"type": "api_error", "message": "x"}},
            extensions={"reason_phrase": f"Broken {secret}".encode()},
        )

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url=f"https://{_LLM_HOST}",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(answer)),
    )
    with debug_logging(caplog), pytest.raises(anthropic.InternalServerError):
        client.messages.create(
            model="claude-test",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )

    assert_that(_everything(caplog)).does_not_contain(secret)
    response = [r for r in caplog.records if str(r.msg).startswith("HTTP Response")]
    assert_that(response).is_length(1)
    arguments = cast(tuple[object, ...], response[0].args)
    assert_that(arguments[2:4]).is_equal_to((500, "Internal Server Error"))


@pytest.mark.parametrize(
    "layer",
    [
        (httpx, httpcore, "httpcore._sync.connection_pool"),
        (httpx2, httpcore2, "httpcore2._sync.connection_pool"),
    ],
    ids=["httpcore", "httpcore2"],
)
def test_transport_trace_prints_protocol_and_status_only(
    layer: tuple[Any, Any, str],
    secret: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The response trace keeps protocol and status; header dumps are not read.

    A private header whose value imitates an allowlisted header pair, and a
    reason phrase, both carry the secret.
    """
    client_module, core, pool_module = layer
    wire = (
        f"HTTP/1.1 200 OK {secret}\r\n"
        f"X-Private: (b'Content-Type', b'{secret}')\r\n"
        "Content-Type: text/plain\r\nContent-Length: 2\r\n\r\nok"
    ).encode()
    monkeypatch.setattr(f"{pool_module}.SyncBackend", lambda: core.MockBackend([wire]))
    with debug_logging(caplog), client_module.Client() as client:
        client.get("http://api.example.test/v1/items")

    assert_that(_everything(caplog)).does_not_contain(secret)
    traces = [
        r.msg for r in caplog.records if "receive_response_headers.complete" in r.msg
    ]
    assert_that(traces).is_equal_to(
        ["receive_response_headers.complete protocol=HTTP/1.1 status=200"],
    )


def _raise_with_literal(request: httpx2.Request) -> httpx2.Response:
    """Fail with a message written literally in the raising statement."""
    raise httpx2.ConnectError("literal-source-secret-0821", request=request)


def test_rebuilt_traceback_has_no_source_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tracebacks keep files, lines, functions and classes, not source."""
    client = anthropic.Anthropic(
        api_key="test-key",
        base_url=f"https://{_LLM_HOST}",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(_raise_with_literal)),
    )
    with debug_logging(caplog, ("_raise_with_literal",)), pytest.raises(
        anthropic.APIConnectionError,
    ):
        client.messages.create(
            model="claude-test",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )

    encountered = [r for r in caplog.records if r.msg == "Encountered Exception"]
    assert_that(encountered).is_length(1)
    text = str(encountered[0].exc_text)
    assert_that(_everything(caplog)).does_not_contain(_SOURCE_MARKER)
    assert_that(text).contains(
        "Traceback (most recent call last):",
        'test_sdk_log_default_deny.py", line ',
        "in [redacted]",
        "httpx2.ConnectError: [message withheld]",
    )
    assert_that(text).does_not_contain("_raise_with_literal", "raise httpx2")


def test_cached_traceback_and_stack_text_are_rebuilt_or_withheld(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """For a known form, cached traceback text is withheld, stack text rebuilt."""
    stack = (
        "Stack (most recent call last):\n"
        '  File "/srv/app.py", line 7, in fetch\n'
        f"    call(token='{secret}')"
    )
    with cached_record_text(f"cached traceback {secret}", stack):
        with debug_logging(caplog):
            logging.getLogger("anthropic._base_client").debug("Encountered Exception")

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.msg).is_equal_to("Encountered Exception")
    assert_that(record.exc_text).is_equal_to("[traceback text withheld]")
    assert_that(record.stack_info).is_equal_to(
        'Stack (most recent call last):\n  File "/srv/app.py", line 7, in fetch',
    )


def _log_with_stack(log: logging.Logger, note: str) -> None:
    """Log with stack text; ``note`` appears only in the caller's source."""
    del note
    log.debug("Encountered Exception", stack_info=True)


def test_real_stack_text_keeps_frames_without_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logged stack keeps file, line and function, not the calling line."""
    with debug_logging(caplog):
        _log_with_stack(
            logging.getLogger("anthropic._base_client"),
            "literal-source-secret-0821",
        )

    record = caplog.records[-1]
    assert_that(str(record.stack_info)).contains(
        "Stack (most recent call last):",
        "in test_real_stack_text_keeps_frames_without_source",
        "in _log_with_stack",
    )
    assert_that(_everything(caplog)).does_not_contain(_SOURCE_MARKER)


@pytest.mark.parametrize(
    "message",
    [
        'Failed to parse the "Expires" member as a timestamp: %s. The unparsed '
        'value is available in the response under "ExpiresString".',
        "Received a tagged union response with member unknown to client: %s. "
        "Please upgrade SDK for full response support.",
        "Found credentials in shared credentials file: %s",
    ],
)
def test_storage_warnings_withhold_unvalidated_values(
    message: str,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The storage client's notices keep their text, values withheld."""
    with debug_logging(caplog):
        logging.getLogger("botocore.handlers").warning(message, f"value {secret}")

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.args).is_equal_to(("[withheld]",))


def test_error_reporting_sdk_records_withhold_response_bodies(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The error-reporting SDK's status lines keep the status, not the body."""
    with debug_logging(caplog):
        log = logging.getLogger("sentry_sdk.errors")
        unexpected = log.makeRecord(
            log.name,
            logging.ERROR,
            "transport.py",
            411,
            "Unexpected status code: %s (body: %s)",
            (429, f"rate limited {secret}".encode()),
            None,
        )
        oversized = log.makeRecord(
            log.name,
            logging.ERROR,
            "transport.py",
            396,
            "HTTP 413: Event dropped due to exceeded envelope size limit "
            f"(body: b'too large {secret}')",
            (),
            None,
        )

    assert_that(unexpected.getMessage()).is_equal_to(
        "Unexpected status code: 429 (body: [withheld])",
    )
    assert_that(oversized.getMessage()).is_equal_to(
        "HTTP 413: Event dropped due to exceeded envelope size limit "
        "(body: [withheld])",
    )
    for record in (unexpected, oversized):
        assert_that(repr(record.args) + str(record.msg)).does_not_contain(secret)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            b"<Error><Code>AccessDenied</Code><Message>private text</Message></Error>",
            "[withheld response body (71 bytes)]",
        ),
        (
            b'{"__type": "com.example#ThrottlingException", "message": "private text"}',
            "[withheld response body (72 bytes)]",
        ),
        (
            b"<Code>bad code with spaces</Code> private text",
            "[withheld response body (46 bytes)]",
        ),
        (b"", "[withheld response body (0 bytes)]"),
    ],
)
def test_storage_response_body_prints_its_size_only(
    body: bytes,
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The storage response record prints the body's size, nothing from it."""
    with debug_logging(caplog):
        logging.getLogger("botocore.parsers").debug("Response body:\n%r", body)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        f"Response body:\n{expected}",
    )
    assert_that(_everything(caplog)).does_not_contain("private text")


@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
@pytest.mark.parametrize(
    ("message", "arguments"),
    [
        pytest.param("params %s", (["x" * 8_192] * 999,), id="wide-strings"),
        pytest.param("%s " * 100_000, tuple(range(100_000)), id="many-placeholders"),
    ],
)
def test_expensive_records_stay_within_budget(
    level: int,
    message: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Records that cost hundreds of milliseconds to inspect are bounded."""
    sdk = logging.getLogger("botocore.parsers")
    with debug_logging(caplog):
        assert_quick_record(sdk, level, message, *arguments)

    assert_that(caplog.records[-1].getMessage()).starts_with(_WITHHELD)


@pytest.mark.parametrize(
    ("logger_name", "call", "expected"),
    [
        (
            "urllib3.connectionpool",
            (logging.DEBUG, "Resetting dropped connection: %s", ("api.example.test",)),
            "Resetting dropped connection: api.example.test",
        ),
        (
            "urllib3.connectionpool",
            (logging.DEBUG, "Retry: %s", ("/v1/items?token=t1",)),
            "Retry: /v1/items?token=[redacted]",
        ),
        (
            "urllib3.poolmanager",
            (
                logging.INFO,
                "Redirecting %s -> %s",
                ("http://a.test/x?code=c1", "http://b.test/y"),
            ),
            "Redirecting http://a.test/x?code=[redacted] -> http://b.test/y",
        ),
        (
            "urllib3.connectionpool",
            (
                logging.WARNING,
                "Connection pool is full, discarding connection: %s. Connection "
                "pool size: %s",
                ("api.example.test", 10),
            ),
            "Connection pool is full, discarding connection: api.example.test. "
            "Connection pool size: 10",
        ),
        (
            "botocore.retries.standard",
            (
                logging.DEBUG,
                "Retry needed, retrying request after delay of: %s",
                (0.5,),
            ),
            "Retry needed, retrying request after delay of: 0.5",
        ),
        (
            "botocore.retries.standard",
            (logging.DEBUG, "Max attempts of %s reached.", ("k1",)),
            "Max attempts of [withheld] reached.",
        ),
        (
            "botocore.auth",
            (logging.DEBUG, "HTTP request method: %s", ("PUT",)),
            "HTTP request method: PUT",
        ),
        (
            "botocore.auth",
            (logging.DEBUG, "String to sign: %s", ("PUT\nx-amz-security-token:t1",)),
            "String to sign: [withheld]",
        ),
        (
            "botocore.credentials",
            (logging.INFO, "Found credentials from IAM Role: %s", ("app-role",)),
            "Found credentials from IAM Role: app-role",
        ),
        (
            "botocore.credentials",
            (logging.INFO, "Found credentials from IAM Role: %s", ("k1 role!",)),
            "Found credentials from IAM Role: [withheld]",
        ),
        (
            "anthropic._response",
            (
                logging.DEBUG,
                "Could not read JSON from response data due to %s - %s",
                (ValueError, ValueError("provider text")),
            ),
            "Could not read JSON from response data due to ValueError - ValueError",
        ),
        (
            "anthropic._base_client",
            (logging.DEBUG, "%i retries left", (2,)),
            "2 retries left",
        ),
        (
            "anthropic._base_client",
            (
                logging.INFO,
                "Retrying request to %s in %f seconds",
                ("/v1/messages?key=k1", 0.5),
            ),
            "Retrying request to /v1/messages?key=[redacted] in 0.500000 seconds",
        ),
        (
            "anthropic._base_client",
            (logging.DEBUG, "workspace_id: %s", ("ws_k1",)),
            "workspace_id: [withheld]",
        ),
        (
            "httpcore.connection",
            (
                logging.DEBUG,
                "connect_tcp.started host='api.example.test' port=443 "
                "local_address=None",
                (),
            ),
            "connect_tcp.started host='api.example.test' port=443",
        ),
        (
            "httpcore2.http11",
            (
                logging.DEBUG,
                "receive_response_headers.failed exception=ReadError('k1')",
                (),
            ),
            "receive_response_headers.failed exception=ReadError",
        ),
        (
            "httpcore2.connection",
            (logging.DEBUG, "close.complete", ()),
            "close.complete",
        ),
        (
            "anthropic._base_client",
            (logging.DEBUG, "Retrying due to status code %i", ("500",)),
            "Retrying due to status code 500",
        ),
        (
            "httpx",
            (logging.DEBUG, "Request options: %s", ({"method": "post"},)),
            f"{_WITHHELD}, 19 characters]",
        ),
        (
            "spotipy.client",
            (logging.ERROR, "HTTP Error for GET to /x with Params: k1", ()),
            f"{_WITHHELD}, 40 characters]",
        ),
        (
            "httpcore.http11",
            (logging.DEBUG, "not a trace line", ()),
            f"{_WITHHELD}, 16 characters]",
        ),
    ],
)
def test_known_forms_print_skeletons_and_other_shapes_are_withheld(
    logger_name: str,
    call: tuple[int, str, tuple[object, ...]],
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each known form prints its skeleton; a shape it does not expect is not."""
    level, message, arguments = call
    with debug_logging(caplog):
        logging.getLogger(logger_name).log(level, message, *arguments)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(expected)
    for value in ("t1", "c1", "k1", "provider text"):
        assert_that(_everything(caplog)).does_not_contain(value)
