"""The third-party SDK log boundary: installation, fail-safety and text rules.

Client-by-client behaviour is tested in ``tests/test_sdk_log_clients.py``.
"""

import logging
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from assertpy import assert_that

from podex import sdk_log_boundary
from podex.logging_config import install_log_boundaries
from podex.sdk_log_boundary import format_withheld_exception, is_sdk_logger
from podex.sdk_log_rules import mask_credential_fields, safe_headers, safe_url
from tests import boundary_sdk_config_process
from tests.process_support import run_in_fresh_process
from tests.sdk_log_support import (
    SDK_REQUEST_LINE,
    debug_logging,
    logged,
    synthetic_secret,
)


def test_boundary_is_installed_once_and_survives_reconfiguration(
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Repeated setup, ``fileConfig`` and ``dictConfig`` keep one boundary."""
    secret = synthetic_secret("reconfigured")

    stdout, stderr = run_in_fresh_process(
        boundary_sdk_config_process.run,
        (secret,),
        capfd,
    )

    assert_that(stdout).contains("boundary wrappers: 1", "httpx filters: 0")
    assert_that(stderr).contains(SDK_REQUEST_LINE)
    assert_that(stdout).contains(
        '127.0.0.1:5000 - "GET /api/v2/podcasts?token=[redacted] HTTP/1.1" 200',
    )
    assert_that(stdout + stderr).does_not_contain(secret)


def test_internal_failure_yields_placeholder_and_no_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If cleaning fails, the record shows a placeholder and nothing original."""
    secret = synthetic_secret("failsafe")

    def broken(_text: str) -> str:
        raise RuntimeError("sanitiser bug")

    with debug_logging(caplog):
        monkeypatch.setattr(sdk_log_boundary, "sanitise_text", broken)
        try:
            raise httpx.ConnectError(f"refused: {secret}")
        except httpx.ConnectError:
            logging.getLogger("httpx").debug(
                "request to %s failed",
                httpx.URL(f"https://reader:{secret}@api.example.test/"),
                exc_info=True,
            )

    output = logged(caplog)
    assert_that(output).does_not_contain(secret)
    assert_that(output).contains(
        "<unprintable: sanitiser failed on str>",
        "<unprintable: sanitiser failed on ConnectError> [ConnectError]",
    )
    assert_that(caplog.records[-1].exc_info).is_none()
    assert_that(caplog.records[-1].args).is_empty()


def test_application_records_are_not_rewritten(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The application's own records keep their message and arguments."""
    with debug_logging(caplog, ("configured-value-1234",)):
        logging.getLogger("podex.services.example").info(
            "fetched %s with token=%s",
            "https://api.example.test/v1",
            "configured-value-1234",
        )

    record = caplog.records[-1]
    assert_that(record.args).is_equal_to(
        ("https://api.example.test/v1", "configured-value-1234"),
    )
    assert_that(record.getMessage()).ends_with("token=configured-value-1234")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("httpx", True),
        ("httpx2", True),
        ("httpcore.http11", True),
        ("anthropic._base_client", True),
        ("botocore.endpoint", True),
        ("urllib3.connectionpool", True),
        ("spotipy.oauth2", True),
        ("uvicorn.access", True),
        ("uvicorn.error", False),
        ("httpxtra", False),
        ("podex.scheduler_runner", False),
        ("root", False),
    ],
)
def test_sdk_logger_families(name: str, expected: bool) -> None:
    """Families match whole name components, children included."""
    assert_that(is_sdk_logger(name)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Headers: {'Authorization': 'Basic YWJjOmRlZg=='} and Body: {}",
            "Headers: {'Authorization': '[redacted]'} and Body: {}",
        ),
        (
            "{'X-Amz-Security-Token': b'tok', 'Content-Type': b'x'}",
            "{'X-Amz-Security-Token': b'[redacted]', 'Content-Type': b'x'}",
        ),
        ('{"access_token": "a\\"b", "status_code": 200}', None),
        (
            "Authorization: AWS4-HMAC-SHA256 Credential=AKIA/x, Signature=abc",
            "Authorization: [redacted], Signature=[redacted]",
        ),
        ("x-amz-security-token:tok\nhost:h", "x-amz-security-token:[redacted]\nhost:h"),
        (
            "/p?token=t&page=2&api_key=k",
            "/p?token=[redacted]&page=2&api_key=[redacted]",
        ),
        ("SSECustomerKey='k1' Bucket='b'", "SSECustomerKey='[redacted]' Bucket='b'"),
        ("token=[redacted]&x=1", "token=[redacted]&x=1"),
        ("status_code: 200, monkey=1, passwordless: on", None),
    ],
)
def test_credential_fields_in_text(text: str, expected: str | None) -> None:
    """Credential-named values are masked; other fields are kept."""
    if expected is None:
        expected = text.replace('"a\\"b"', '"[redacted]"')
    assert_that(mask_credential_fields(text)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:pw@api.example.test/v1?api%5Fkey=k&page=2#frag",
            "https://[redacted]@api.example.test/v1?api%5Fkey=[redacted]&page=2"
            "#[redacted]",
        ),
        ("/v1/messages?Signature=s", "/v1/messages?Signature=[redacted]"),
        ("file:///tmp/x", "file:///tmp/x"),
        ("http://[::1", "http://[::1"),
    ],
)
def test_safe_url(url: str, expected: str) -> None:
    """URLs keep scheme, host, path and other fields; credentials are masked."""
    assert_that(safe_url(url)).is_equal_to(expected)


def test_safe_headers_keep_names_and_allowlisted_values() -> None:
    """Every header name stays; only allowlisted values are shown."""
    headers = httpx.Headers(
        [
            ("Content-Type", "application/json"),
            ("Set-Cookie", "a=1"),
            ("Set-Cookie", "b=2"),
            ("anthropic-ratelimit-requests-remaining", "9"),
        ],
    )

    assert_that(safe_headers(headers)).is_equal_to(
        {
            "content-type": "application/json",
            "set-cookie": "[redacted], [redacted]",
            "anthropic-ratelimit-requests-remaining": "9",
        },
    )
    assert_that(safe_headers([(b"X-Api-Key", b"k"), ("bad",)])).is_equal_to(
        {b"X-Api-Key": "[redacted]"},
    )
    assert_that(safe_headers(42)).is_empty()


def _raise_chained_group(secret: str) -> None:
    """Raise an exception group caused by an error, each quoting ``secret``."""
    try:
        raise OSError(f"socket said {secret}")
    except OSError as cause:
        raise ExceptionGroup(f"group {secret}", [ValueError(secret)]) from cause


def test_withheld_traceback_keeps_frames_classes_and_chain() -> None:
    """A logged traceback keeps its shape and loses every message."""
    secret = synthetic_secret("chained")
    with pytest.raises(ExceptionGroup) as caught:
        _raise_chained_group(secret)

    text = format_withheld_exception(caught.value)

    assert_that(text).does_not_contain(secret)
    assert_that(text).contains(
        "OSError: [message withheld]",
        "The above exception was the direct cause of the following exception:",
        "ExceptionGroup: [message withheld] (1 sub-exception)",
        "| ValueError: [message withheld]",
        "test_sdk_log_boundary.py",
    )


def test_record_shapes_are_rendered_safely(caplog: pytest.LogCaptureFixture) -> None:
    """Named arguments, a lone mapping, objects, exceptions and nesting limits.

    The secret is also a configured value, so it is replaced wherever it
    appears as plain text.
    """
    secret = synthetic_secret("shapes")
    with debug_logging(caplog, (secret,)):
        sdk = logging.getLogger("botocore.endpoint")
        sdk.debug("%(method)s %(token)s", {"method": "GET", "token": secret})
        sdk.debug("params: %s", {"headers": {"Cookie": secret}, "body": secret})
        sdk.debug(ValueError(secret))
        sdk.debug(
            "response: %r and %s",
            httpx.Response(503, headers={"X-Trace": secret}),
            [secret.encode(), (secret, {"nested": [{"deep": {"deeper": secret}}]})],
        )

    output = logged(caplog)
    assert_that(output).does_not_contain(secret)
    assert_that(output).contains(
        "GET [redacted]",
        "params: {'headers': {'Cookie': '[redacted]'}, 'body': '[withheld str",
        "ValueError: [message withheld]",
        "<Response status=503 headers={'x-trace': '[redacted]'}>",
        "[withheld bytes",
        "('[redacted]', {'nested': '<unprintable: sanitiser limit reached on list>'})",
    )


def _raise_group(secret: str) -> None:
    """Raise an exception group whose members quote ``secret``."""
    raise ExceptionGroup(
        f"two failures {secret}",
        [ValueError(f"bad value {secret}"), KeyError(secret)],
    )


def test_sdk_exception_group_keeps_member_classes_and_frames(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A logged group prints each member's class and frames, no message."""
    secret = synthetic_secret("group")
    with debug_logging(caplog):
        try:
            _raise_group(secret)
        except ExceptionGroup:
            logging.getLogger("anthropic._base_client").debug(
                "Encountered Exception",
                exc_info=True,
            )

    output = logged(caplog)
    record = caplog.records[-1]
    assert_that(output).does_not_contain(secret)
    assert_that(record.exc_info).is_none()
    assert_that(record.exc_text).contains(
        "ExceptionGroup: [message withheld] (2 sub-exceptions)",
        "+---------------- 1 ----------------",
        "| ValueError: [message withheld]",
        "| KeyError: [message withheld]",
        "in _raise_group",
    )


@pytest.mark.parametrize("logger_name", ["httpcore.http11", "botocore.endpoint"])
@pytest.mark.parametrize(
    ("message", "masked"),
    [
        (
            "Headers: {'Authorization': '%s'}",
            "Headers: {'Authorization': '[redacted]'}",
        ),
        ("Authorization: Bearer %s", "Authorization: [redacted]"),
        (
            '{"access_token": "%s", "page": 2}',
            '{"access_token": "[redacted]", "page": 2}',
        ),
        ("callback ?%%63ode=%s&state=x", "callback ?%63ode=[redacted]&state=x"),
    ],
)
def test_placeholders_inside_quotes_or_after_a_prefix_are_masked(
    logger_name: str,
    message: str,
    masked: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rendered message is scanned whole, so split fields are masked."""
    secret = synthetic_secret("split")
    with debug_logging(caplog):
        logging.getLogger(logger_name).debug(message, secret)

    record = caplog.records[-1]
    assert_that(logged(caplog)).does_not_contain(secret)
    assert_that(record.msg).is_equal_to(masked)
    assert_that(record.args).is_empty()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Headers: {'Authorization': 'Basic YWJjOmRlZg=='} and Body: {}",
            "Headers: {'Authorization': '[redacted]'} and Body: {}",
        ),
        (
            "Headers: {'Authorization': 'Bearer tok', 'Content-Type': 'x'}",
            "Headers: {'Authorization': '[redacted]', 'Content-Type': 'x'}",
        ),
        (
            "Body: {'refresh_token': 'r1', 'code': 'c1', 'status_code': 200}",
            "Body: {'refresh_token': '[redacted]', 'code': '[redacted]', "
            "'status_code': 200}",
        ),
    ],
)
@pytest.mark.parametrize("shape", ["formatted", "argument"])
def test_former_spotify_filter_cases_hold_through_the_boundary(
    message: str,
    expected: str,
    shape: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What the removed Spotify filter masked is still masked, either way."""
    with debug_logging(caplog):
        spotify = logging.getLogger("spotipy.oauth2")
        if shape == "formatted":
            spotify.debug(message)
        else:
            spotify.debug("%s", message)

    assert_that(caplog.records[-1].getMessage()).is_equal_to(expected)
    for value in ("YWJjOmRlZg==", "tok'", "r1", "c1"):
        assert_that(logged(caplog)).does_not_contain(value)


def test_failure_clears_cached_traceback_and_stack_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If cleaning fails, cached text on the record is replaced as well."""
    secret = synthetic_secret("cached")
    previous = logging.getLogRecordFactory()

    def caching_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = logging.LogRecord(*args, **kwargs)
        record.exc_text = f"cached traceback {secret}"
        record.stack_info = f"cached stack {secret}"
        return record

    def broken(_text: str) -> str:
        raise RuntimeError("sanitiser bug")

    logging.setLogRecordFactory(caching_factory)
    try:
        install_log_boundaries()
        with debug_logging(caplog):
            monkeypatch.setattr(sdk_log_boundary, "sanitise_text", broken)
            logging.getLogger("httpx").debug("request %s", secret)
    finally:
        logging.setLogRecordFactory(previous)

    record = caplog.records[-1]
    assert_that(logged(caplog)).does_not_contain(secret)
    assert_that(record.exc_text).is_equal_to("<unprintable: sanitiser failed on str>")
    assert_that(record.stack_info).is_none()
    assert_that(record.exc_info).is_none()
    assert_that(record.args).is_empty()


_WIDE = 100_000


@pytest.mark.parametrize(
    ("message", "argument"),
    [
        pytest.param("x" * _WIDE + " token=%s", "s", id="wide-literal"),
        pytest.param("x" * _WIDE + " %s", "s", id="wide-unlabelled-literal"),
        pytest.param("value: %s", "y" * _WIDE, id="wide-string"),
        pytest.param(
            "Response headers: %r",
            httpx.Headers([(f"x-h{index}", "v") for index in range(_WIDE)]),
            id="wide-headers",
        ),
        pytest.param(
            "params: %s",
            {f"k{index}": index for index in range(_WIDE)},
            id="wide-mapping",
        ),
        pytest.param("items: %s", list(range(_WIDE)), id="wide-list"),
    ],
)
def test_wide_records_are_cleaned_in_bounded_time(
    message: str,
    argument: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strings and containers are read up to a bound, never in full."""
    sdk = logging.getLogger("botocore.endpoint")

    def log() -> None:
        sdk.debug(message, argument)

    with debug_logging(caplog):
        log()
        started = time.perf_counter()
        log()
        elapsed = time.perf_counter() - started

    assert_that(elapsed).is_less_than(0.1)
    assert_that(len(caplog.records[-1].getMessage())).is_less_than(20_000)


class _CountingHeaders(Mapping[str, str]):
    """A large header mapping that counts the entries read from it."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.read = 0

    def __getitem__(self, key: str) -> str:
        self.read += 1
        return "v"

    def __iter__(self) -> Iterator[str]:
        for index in range(self.size):
            self.read += 1
            yield f"x-h{index}"

    def __len__(self) -> int:
        return self.size


@dataclass
class _PreparedRequest:
    """A prepared request as an SDK logs it, with a large header container."""

    headers: _CountingHeaders
    method: str = "PUT"
    url: str = "https://storage.example.test/artifacts/k"


def test_wide_header_container_is_not_enumerated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A request's header container past the item bound is never read."""
    headers = _CountingHeaders(_WIDE)
    with debug_logging(caplog):
        logging.getLogger("botocore.endpoint").debug(
            "Sending http request: %s",
            _PreparedRequest(headers),
        )

    assert_that(headers.read).is_equal_to(0)
    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        "Sending http request: <_PreparedRequest method=PUT "
        "url=https://storage.example.test/artifacts/k "
        "headers=<unprintable: sanitiser limit reached on _CountingHeaders>>",
    )
