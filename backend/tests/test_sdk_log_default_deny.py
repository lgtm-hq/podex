"""Third-party DEBUG output is allowlisted by form; the rest is withheld.

Each test configures logging as the application does and searches the
formatted output and the raw records (``msg``, ``args``, ``exc_text``,
``exc_info``, ``stack_info``). Real clients run over fake transports or
stubs; where a test logs an SDK record directly, it uses the exact form the
SDK logs.
"""

import logging
import time
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

from podex.logging_config import install_log_boundaries
from podex.redaction import REDACTED
from podex.sdk_log_rules import mask_credential_fields
from tests.sdk_log_support import debug_logging, logged, synthetic_secret

_LLM_HOST = "llm.example.test"
_SOURCE_MARKER = "literal-source-secret-0821"


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
    ],
)
def test_unrecognised_debug_records_are_withheld(
    logger_name: str,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unknown DEBUG record keeps logger, level and size, nothing else."""
    with debug_logging(caplog):
        try:
            raise ValueError(secret)
        except ValueError:
            logging.getLogger(logger_name).debug(
                "detail: %s",
                {"note": secret},
                exc_info=True,
                stack_info=True,
            )

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.name).is_equal_to(logger_name)
    assert_that(record.levelno).is_equal_to(logging.DEBUG)
    assert_that(record.msg).is_equal_to(
        "[debug record withheld: unrecognised form, 10 characters]",
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
        "[debug record withheld: unrecognised form, 5 characters]",
    )


def test_storage_credential_names_are_masked_at_info(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Credential provider field names are credentials wherever they appear."""
    with debug_logging(caplog):
        logging.getLogger("botocore.credentials").warning(
            "provider response: %s",
            {
                "AccessKeyId": f"AKIA{secret}",
                "SecretAccessKey": secret,
                "Token": secret,
            },
        )

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.args).is_equal_to(
        (
            {
                "AccessKeyId": REDACTED,
                "SecretAccessKey": REDACTED,
                "Token": REDACTED,
            },
        ),
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
def test_transport_trace_drops_the_reason_phrase(
    layer: tuple[Any, Any, str],
    secret: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The response trace keeps protocol, status and allowlisted headers."""
    client_module, core, pool_module = layer
    wire = (
        f"HTTP/1.1 200 OK {secret}\r\nContent-Type: text/plain\r\n"
        "Content-Length: 2\r\n\r\nok"
    ).encode()
    monkeypatch.setattr(f"{pool_module}.SyncBackend", lambda: core.MockBackend([wire]))
    with debug_logging(caplog), client_module.Client() as client:
        client.get("http://api.example.test/v1/items")

    assert_that(_everything(caplog)).does_not_contain(secret)
    traces = [
        r.msg for r in caplog.records if "receive_response_headers.complete" in r.msg
    ]
    assert_that(traces).is_equal_to(
        [
            "receive_response_headers.complete protocol=HTTP/1.1 status=200 "
            "headers={'Content-Type': 'text/plain', 'Content-Length': '2'}",
        ],
    )


@pytest.mark.parametrize(
    ("message", "masked"),
    [
        (
            "retrying with Authorization=Bearer {s}&attempt=2",
            "retrying with Authorization=[redacted]&attempt=2",
        ),
        ("header Authorization: Basic {s}", "header Authorization: [redacted]"),
        ("{{'Authorization': 'Bearer {s}'}}", "{'Authorization': '[redacted]'}"),
        (
            "proxy-authorization=Digest {s}, next",
            "proxy-authorization=[redacted], next",
        ),
    ],
)
def test_authorization_values_are_masked_whole(
    message: str,
    masked: str,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Scheme and credential go together, in each field form."""
    text = message.format(s=secret)
    with debug_logging(caplog):
        logging.getLogger("urllib3.connectionpool").warning(text)
        logging.getLogger("urllib3.connectionpool").warning(
            "auth Authorization=Bearer %s done",
            secret,
        )

    first, second = caplog.records[-2:]
    assert_that(mask_credential_fields(text)).is_equal_to(masked)
    assert_that(first.msg).is_equal_to(masked)
    assert_that(second.msg).is_equal_to("auth Authorization=[redacted] done")
    assert_that(_everything(caplog)).does_not_contain(secret)


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
    """Cached traceback text is withheld; stack text keeps frame lines only."""
    previous = logging.getLogRecordFactory()

    def caching_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = logging.LogRecord(*args, **kwargs)
        record.exc_text = f"cached traceback {secret}"
        record.stack_info = (
            "Stack (most recent call last):\n"
            '  File "/srv/app.py", line 7, in fetch\n'
            f"    call(token='{secret}')"
        )
        return record

    logging.setLogRecordFactory(caching_factory)
    try:
        install_log_boundaries()
        with debug_logging(caplog):
            logging.getLogger("httpx").warning("request failed")
    finally:
        logging.setLogRecordFactory(previous)

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.exc_text).is_equal_to("[traceback text withheld]")
    assert_that(record.stack_info).is_equal_to(
        'Stack (most recent call last):\n  File "/srv/app.py", line 7, in fetch',
    )


def _log_with_stack(log: logging.Logger, note: str) -> None:
    """Log with stack text; ``note`` appears only in the caller's source."""
    del note
    log.warning("request", stack_info=True)


def test_real_stack_text_keeps_frames_without_source(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logged stack keeps file, line and function, not the calling line."""
    with debug_logging(caplog):
        _log_with_stack(logging.getLogger("httpx"), "literal-source-secret-0821")

    record = caplog.records[-1]
    assert_that(str(record.stack_info)).contains(
        "Stack (most recent call last):",
        "in test_real_stack_text_keeps_frames_without_source",
        "in _log_with_stack",
    )
    assert_that(_everything(caplog)).does_not_contain(_SOURCE_MARKER)


def test_rendered_header_text_is_withheld(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A header container already rendered to text cannot be inspected."""
    rendered = str({"x-amz-request-id": "r1", "x-provider-trace": secret})
    with debug_logging(caplog):
        logging.getLogger("botocore.parsers").warning("Response headers: %s", rendered)

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.getMessage()).is_equal_to(
        f"Response headers: [withheld str ({len(rendered)})]",
    )


def test_oversized_values_are_withheld_whole(
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A long value is withheld by type and size, never cut to a prefix."""
    value = "a" * 8_180 + secret + "b" * 100
    with debug_logging(caplog, (secret,)):
        logging.getLogger("botocore.endpoint").warning("value: %s", value)

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret[:8])
    assert_that(record.getMessage()).is_equal_to(
        f"value: [withheld str ({len(value)})]"
    )


@pytest.mark.parametrize(
    "message",
    [
        'Failed to parse the "Expires" member as a timestamp: %s. The unparsed '
        'value is available in the response under "ExpiresString".',
        "Received a tagged union response with member unknown to client: %s. "
        "Please upgrade SDK for full response support.",
    ],
)
def test_storage_warnings_withhold_response_values(
    message: str,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The storage client's warnings quoting a response value withhold it."""
    with debug_logging(caplog):
        logging.getLogger("botocore.handlers").warning(message, f"value {secret}")

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.args).is_equal_to((f"[withheld str ({len(secret) + 6})]",))


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
        sdk.log(level, message, *arguments)
        started = time.perf_counter()
        sdk.log(level, message, *arguments)
        elapsed = time.perf_counter() - started

    assert_that(elapsed).is_less_than(0.05)
    assert_that(caplog.records[-1].getMessage()).starts_with("[")


_WITHHELD = "[debug record withheld: unrecognised form"


@pytest.mark.parametrize(
    ("logger_name", "call", "expected"),
    [
        (
            "urllib3.connectionpool",
            (
                logging.DEBUG,
                "Resetting dropped connection: %s",
                ("api.example.test",),
            ),
            "Resetting dropped connection: api.example.test",
        ),
        (
            "urllib3.connectionpool",
            (
                logging.DEBUG,
                "Retry: %s",
                ("/v1/items?token=t1",),
            ),
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
            (
                logging.DEBUG,
                "Max attempts of %s reached.",
                (3,),
            ),
            "Max attempts of 3 reached.",
        ),
        (
            "botocore.auth",
            (
                logging.DEBUG,
                "HTTP request method: %s",
                ("PUT",),
            ),
            "HTTP request method: PUT",
        ),
        (
            "botocore.auth",
            (
                logging.DEBUG,
                "String to sign: %s",
                ("PUT\n\nx-amz-security-token:t1\n/b/k",),
            ),
            "String to sign: PUT\n\nx-amz-security-token:[redacted]\n/b/k",
        ),
        (
            "anthropic._response",
            (
                logging.DEBUG,
                "Could not read JSON from response data due to %s - %s",
                (ValueError, ValueError("provider text")),
            ),
            "Could not read JSON from response data due to ValueError - "
            "ValueError: [message withheld]",
        ),
        (
            "anthropic._base_client",
            (
                logging.DEBUG,
                "%i retries left",
                (2,),
            ),
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
            "httpcore2.connection",
            (
                logging.DEBUG,
                "close.complete",
                (),
            ),
            "close.complete",
        ),
        (
            "anthropic._base_client",
            (
                logging.DEBUG,
                "Retrying due to status code %i",
                ("500",),
            ),
            f"{_WITHHELD}, 30 characters]",
        ),
        (
            "httpx",
            (
                logging.DEBUG,
                "Request options: %s",
                ({"method": "post"},),
            ),
            f"{_WITHHELD}, 19 characters]",
        ),
        (
            "urllib3.connectionpool",
            (
                logging.DEBUG,
                '%s://%s:%s "%s %s %s" %s %s',
                ("ftp", "h", 21, "GET", "/", "1.1", 200, 0),
            ),
            f"{_WITHHELD}, 27 characters]",
        ),
        (
            "spotipy.client",
            (
                logging.DEBUG,
                "Unknown line with provider text",
                (),
            ),
            f"{_WITHHELD}, 31 characters]",
        ),
        (
            "httpcore.http11",
            (
                logging.DEBUG,
                "not a trace line",
                (),
            ),
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
