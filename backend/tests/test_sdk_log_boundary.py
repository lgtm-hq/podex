"""The third-party log boundary: installation, fail-safety and validators.

Client-by-client behaviour is tested in ``tests/test_sdk_log_clients.py``
and ``tests/test_sdk_log_default_deny.py``.
"""

import logging
import time
from collections.abc import Iterator, Mapping

import httpx
import pytest
from assertpy import assert_that
from botocore.awsrequest import AWSPreparedRequest

from podex import logging_config, sdk_log_boundary
from podex.sdk_log_boundary import format_withheld_exception, is_sdk_logger
from podex.sdk_log_rules import (
    headers_skeleton,
    is_credential_key,
    safe_header_name,
    url_skeleton,
)
from tests import boundary_sdk_config_process
from tests.process_support import run_in_fresh_process
from tests.sdk_log_support import (
    SDK_REQUEST_LINE,
    cached_record_text,
    debug_logging,
    logged,
    synthetic_secret,
)

_WITHHELD = "[record withheld: unrecognised form"


def _everything(caplog: pytest.LogCaptureFixture) -> str:
    """Return everything the records could print, stack text included."""
    stacks = [str(record.stack_info) for record in caplog.records]
    return "\n".join([logged(caplog), *stacks])


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


def _broken(*_args: object) -> object:
    """Fail as an internal error of the boundary would."""
    raise RuntimeError("sanitiser bug")


def test_internal_failure_yields_placeholder_and_no_content(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the boundary fails, the record shows a placeholder, nothing original."""
    secret = synthetic_secret("failsafe")

    with debug_logging(caplog):
        monkeypatch.setattr(sdk_log_boundary, "known_form", _broken)
        try:
            raise httpx.ConnectError(f"refused: {secret}")
        except httpx.ConnectError:
            logging.getLogger("httpx").warning(
                "request to %s failed",
                httpx.URL(f"https://reader:{secret}@api.example.test/"),
                exc_info=True,
                stack_info=True,
            )

    record = caplog.records[-1]
    output = _everything(caplog)
    assert_that(output).does_not_contain(secret)
    assert_that(output).contains(
        "<unprintable: sanitiser failed on str>",
        "<unprintable: sanitiser failed on ConnectError> [ConnectError]",
    )
    assert_that(record.exc_info).is_none()
    assert_that(record.args).is_empty()
    assert_that(record.stack_info).is_none()


def test_failing_placeholder_leaves_constants_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If building the placeholder fails too, only constants remain."""
    secret = synthetic_secret("emergency")

    with debug_logging(caplog):
        monkeypatch.setattr(sdk_log_boundary, "known_form", _broken)
        monkeypatch.setattr(logging_config, "sanitise_database_errors", _broken)
        monkeypatch.setattr(logging_config, "placeholder", _broken)
        for name in ("httpx", "podex.services.example"):
            try:
                raise ValueError(secret)
            except ValueError:
                logging.getLogger(name).error(
                    "failed %s",
                    secret,
                    exc_info=True,
                    stack_info=True,
                )

    assert_that(_everything(caplog)).does_not_contain(secret)
    for record in caplog.records[-2:]:
        assert_that(record.msg).is_equal_to("<unprintable: sanitiser failed>")
        assert_that(record.args).is_empty()
        assert_that(record.exc_info).is_none()
        assert_that(record.exc_text).is_none()
        assert_that(record.stack_info).is_none()


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
        ("charset_normalizer", True),
        ("spotipy.oauth2", True),
        ("sentry_sdk.errors", True),
        ("pydantic_settings", True),
        ("bs4.dammit", True),
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
    ("name", "expected"),
    [
        ("token", True),
        ("ToKeN", True),
        ("AWSAccessKeyId", True),
        ("AccessKeyId", True),
        ("SecretAccessKey", True),
        ("SessionToken", True),
        ("x-amz-security-token", True),
        ("api%5Fkey", True),
        ("SSECustomerKey", True),
        ("status_code", False),
        ("monkey", False),
        ("request-id", False),
    ],
)
def test_credential_names(name: str, expected: bool) -> None:
    """Credential names match in any case, spelling or encoding."""
    assert_that(is_credential_key(name)).is_equal_to(expected)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Content-Type", "content-type"),
        ("X-Ratelimit-Remaining", "x-ratelimit-remaining"),
        ("X-Ratelimit-Token", None),
        ("anthropic-ratelimit-api-key", None),
        ("X-Debug-Trace", None),
        ("Authorization", None),
        ("x-ratelimit-bad name", None),
    ],
)
def test_header_allowlist_rejects_credential_names_first(
    name: str,
    expected: str | None,
) -> None:
    """A credential-named header is never allowlisted, even by prefix."""
    assert_that(safe_header_name(name)).is_equal_to(expected)


def test_headers_skeleton_prints_allowlisted_names_and_counts_the_rest() -> None:
    """Only allowlisted names print; values are validated; others counted."""
    headers = httpx.Headers(
        [
            ("Content-Type", "application/json"),
            ("Set-Cookie", "a=1"),
            ("X-Debug-Thing", "x"),
            ("Server", "<script>"),
            ("anthropic-ratelimit-requests-remaining", "9"),
            ("Date", "d" * 200),
        ],
    )

    assert_that(headers_skeleton(headers)).is_equal_to(
        "{'content-type': 'application/json', 'server': '[withheld]', "
        "'anthropic-ratelimit-requests-remaining': '9', 'date': '[withheld]'} "
        "(+2 other headers)",
    )
    assert_that(headers_skeleton([(b"X-Api-Key", b"k"), ("bad",)])).is_equal_to(
        "{} (+2 other headers)",
    )
    assert_that(headers_skeleton(42)).is_equal_to("{}")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:pw@api.example.test:8443/v1?api%5Fkey=k&page=2#frag",
            "https://api.example.test:8443/v1?api_key=[redacted]&page=2",
        ),
        (
            "/v1/messages?Signature=s&q=hello world",
            "/v1/messages?Signature=[redacted]&q=[withheld]",
        ),
        ("/v1/items?a%20b=1&flag", "/v1/items?[withheld]&flag"),
        ("/reset/<script>", "/[withheld]"),
        ("ftp://files.example.test/x", "[withheld]"),
        ("http://[::1", "[withheld]"),
        ("/v1?" + "&".join(f"f{index}=1" for index in range(20)), None),
    ],
)
def test_url_skeleton(url: str, expected: str | None) -> None:
    """URLs keep validated parts only; credential values are masked."""
    if expected is None:
        expected = "/v1?" + "&".join(f"f{index}=1" for index in range(16))
        expected += "&[+4 more fields]"
    assert_that(url_skeleton(url)).is_equal_to(expected)


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


@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING, logging.ERROR])
@pytest.mark.parametrize(
    ("logger_name", "message"),
    [
        ("httpcore.http11", "Headers: {'Authorization': '%s'}"),
        ("botocore.endpoint", "Authorization: Bearer %s"),
        ("urllib3.connectionpool", 'payload {"access_token": "%s"}'),
        ("spotipy.oauth2", "callback ?%%63ode=%s&state=x"),
        ("anthropic._base_client", "%(method)s %(token)s"),
        ("sentry_sdk.envelopes", "detail: %s"),
        ("pydantic_settings", "settings: %s"),
    ],
)
def test_unrecognised_records_are_withheld_at_every_level(
    level: int,
    logger_name: str,
    message: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record that is not a known form keeps logger, level and size only."""
    secret = synthetic_secret("unknown")
    arguments: tuple[object, ...] = (secret,)
    if "%(" in message:
        arguments = ({"method": "GET", "token": secret},)
    with debug_logging(caplog):
        logging.getLogger(logger_name).log(level, message, *arguments)

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.levelno).is_equal_to(level)
    assert_that(record.msg).is_equal_to(f"{_WITHHELD}, {len(message)} characters]")
    assert_that(record.args).is_empty()


def test_withheld_record_names_exception_classes_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A withheld record with an exception names its classes, nothing else."""
    secret = synthetic_secret("classes")
    with debug_logging(caplog):
        try:
            try:
                raise KeyError(secret)
            except KeyError as cause:
                raise httpx.ConnectError(secret) from cause
        except httpx.ConnectError:
            logging.getLogger("urllib3.connection").error("broken", exc_info=True)

    record = caplog.records[-1]
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(record.msg).is_equal_to(
        f"{_WITHHELD}, 6 characters; exception httpx.ConnectError, KeyError]",
    )
    assert_that(record.exc_info).is_none()
    assert_that(record.exc_text).is_none()


def _raise_group(secret: str) -> None:
    """Raise an exception group whose members quote ``secret``."""
    raise ExceptionGroup(
        f"two failures {secret}",
        [ValueError(f"bad value {secret}"), KeyError(secret)],
    )


def test_sdk_exception_group_keeps_member_classes_and_frames(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A known form's group prints each member's class and frames, no message."""
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


def test_failure_clears_cached_traceback_and_stack_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the boundary fails, cached text on the record is replaced as well."""
    secret = synthetic_secret("cached")
    with cached_record_text(f"cached traceback {secret}", f"cached stack {secret}"):
        with debug_logging(caplog):
            monkeypatch.setattr(sdk_log_boundary, "known_form", _broken)
            logging.getLogger("httpx").warning("request %s", secret)

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
@pytest.mark.parametrize("level", [logging.DEBUG, logging.WARNING])
def test_wide_records_are_cleaned_in_bounded_time(
    level: int,
    message: str,
    argument: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strings and containers are read up to a bound, never in full."""
    sdk = logging.getLogger("botocore.endpoint")

    def log() -> None:
        sdk.log(level, message, argument)

    with debug_logging(caplog):
        log()
        started = time.perf_counter()
        log()
        elapsed = time.perf_counter() - started

    assert_that(elapsed).is_less_than(0.05)
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


def test_wide_header_container_is_not_enumerated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A request's header container past the item bound is never read."""
    headers = _CountingHeaders(_WIDE)
    with debug_logging(caplog):
        logging.getLogger("botocore.endpoint").debug(
            "Sending http request: %s",
            AWSPreparedRequest(
                method="PUT",
                url="https://storage.example.test/artifacts/k",
                headers=headers,
                body=None,
                stream_output=False,
            ),
        )

    assert_that(headers.read).is_equal_to(0)
    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        "Sending http request: <botocore.awsrequest.AWSPreparedRequest "
        "method=PUT url=https://storage.example.test/artifacts/k "
        f"headers={{}} (+{_WIDE} other headers)>",
    )


def test_many_long_allowlisted_headers_are_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A thousand long allowlisted headers print a bounded skeleton quickly."""
    headers = {f"x-ratelimit-limit-{index}": "v" * 8_192 for index in range(1_000)}
    sdk = logging.getLogger("botocore.parsers")
    with debug_logging(caplog):
        sdk.debug("Response headers: %r", headers)
        started = time.perf_counter()
        sdk.debug("Response headers: %r", headers)
        elapsed = time.perf_counter() - started

    message = caplog.records[-1].getMessage()
    assert_that(elapsed).is_less_than(0.05)
    assert_that(len(message)).is_less_than(2_000)
    assert_that(message).ends_with("(+984 other headers)")
    assert_that(message).does_not_contain("v" * 200)


def test_header_names_are_printed_only_from_the_allowlist(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A header name carrying a secret, or credential-named, is not printed."""
    secret = synthetic_secret("name")
    headers = {
        f"X-Debug-{secret}": "1",
        "X-Ratelimit-Token": secret,
        f"x-ratelimit-{secret}": "2",
        "Content-Type": "text/plain",
    }
    with debug_logging(caplog, (secret,)):
        logging.getLogger("botocore.parsers").debug("Response headers: %r", headers)

    message = caplog.records[-1].getMessage()
    assert_that(_everything(caplog)).does_not_contain(secret)
    assert_that(message).starts_with(
        "Response headers: {'x-ratelimit-[redacted]': '2', 'content-type': "
        "'text/plain'} (+2 other headers)",
    )
