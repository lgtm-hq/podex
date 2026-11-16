"""Each size and shape bound of the third-party log filter, at and one past it.

Every test asserts on what a record prints, never on how long it takes: an
input exactly at a bound prints its normal form, and an input one unit past
it prints the withheld form the bound specifies. The bounds are imported
from the modules that define them, so a deliberate change of value moves the
inputs with it, while removing a check lets the one-past input print.
"""

import logging
import sys

import pytest
from assertpy import assert_that

from podex.sdk_log_boundary import MAX_SKELETON
from podex.sdk_log_forms import FORMS, MAX_PREFORMATTED, SERVER_FORMS
from podex.sdk_log_rules import MAX_HEADER_VALUE, WITHHELD, header_value
from tests.sdk_log_support import debug_logging

_EXTRA = "k1-extra"
"""An argument past a form's arity."""

_NON_ASCII = "\N{LATIN SMALL LETTER E WITH ACUTE}"
"""One character, two bytes in UTF-8: bounds count characters, not bytes."""


def _unrecognised(length: int) -> str:
    """Return the marker of a record withheld as an unrecognised form."""
    return f"[record withheld: unrecognised form, {length} characters]"


def _printed(
    caplog: pytest.LogCaptureFixture,
    logger_name: str,
    message: str,
    *arguments: object,
) -> str:
    """Log one record as the application does and return what it prints."""
    with debug_logging(caplog):
        logging.getLogger(logger_name).info(message, *arguments)
    printed: str = caplog.records[-1].getMessage()
    return printed


_KNOWN_FORMS: dict[str, tuple[str, str, tuple[object, ...], str]] = {
    "numbers": (
        "anthropic._base_client",
        "Retrying due to status code %i",
        (429,),
        "Retrying due to status code 429",
    ),
    "parts": (
        "urllib3.connectionpool",
        "Starting new HTTPS connection (%d): %s:%s",
        (1, "api.example.test", 443),
        "Starting new HTTPS connection (1): api.example.test:443",
    ),
    "one-part": (
        "botocore.auth",
        "HTTP request method: %s",
        ("PUT",),
        "HTTP request method: PUT",
    ),
    "constant": (
        "botocore.auth",
        "Calculating signature using v4 auth.",
        (),
        "Calculating signature using v4 auth.",
    ),
}
"""Known forms by handler kind: logger, format string, arguments, skeleton."""


@pytest.mark.parametrize(
    ("logger_name", "message", "arguments", "expected"),
    [pytest.param(*case, id=kind) for kind, case in _KNOWN_FORMS.items()],
)
def test_known_form_with_its_arity_prints_its_skeleton(
    logger_name: str,
    message: str,
    arguments: tuple[object, ...],
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A known form with exactly its number of arguments is printed."""
    assert_that(FORMS[message][1]).is_equal_to(len(arguments))

    printed = _printed(caplog, logger_name, message, *arguments)

    assert_that(printed).is_equal_to(expected)


@pytest.mark.parametrize(
    ("logger_name", "message", "arguments"),
    [
        pytest.param(logger_name, message, arguments, id=kind)
        for kind, (logger_name, message, arguments, _) in _KNOWN_FORMS.items()
    ],
)
def test_known_form_with_one_argument_too_many_is_withheld(
    logger_name: str,
    message: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One argument past a known form's arity withholds the record whole."""
    assert_that(FORMS[message][1]).is_equal_to(len(arguments))

    printed = _printed(caplog, logger_name, message, *arguments, _EXTRA)

    assert_that(printed).is_equal_to(_unrecognised(len(message)))


@pytest.mark.parametrize(
    ("logger_name", "message", "arguments"),
    [
        pytest.param(logger_name, message, arguments, id=kind)
        for kind, (logger_name, message, arguments, _) in _KNOWN_FORMS.items()
        if arguments
    ],
)
def test_known_form_with_one_argument_too_few_is_withheld(
    logger_name: str,
    message: str,
    arguments: tuple[object, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One argument short of a known form's arity withholds the record whole."""
    assert_that(FORMS[message][1]).is_equal_to(len(arguments))

    printed = _printed(caplog, logger_name, message, *arguments[:-1])

    assert_that(printed).is_equal_to(_unrecognised(len(message)))


_SPOTIFY_HEAD = "Sending GET to https://api.spotify.com/v1/me/shows with Params: "
_SPOTIFY_SKELETON = "Sending GET to https://api.spotify.com/v1/me/shows"


def test_preformatted_line_with_an_argument_is_withheld(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A line a library formats itself is known only when it has no arguments."""
    message = f"{_SPOTIFY_HEAD}%s"

    printed = _printed(caplog, "spotipy.client", message, _EXTRA)

    assert_that(printed).is_equal_to(_unrecognised(len(message)))


_SERVER_FORMS: dict[str, tuple[str, tuple[object, ...], str, str]] = {
    "header": (
        "< %s: %s",
        ("content-type", "text/plain"),
        f"< content-type: {WITHHELD}",
        f"< {WITHHELD}: {WITHHELD}",
    ),
    "handshake": (
        '%s - "WebSocket %s" %d',
        ("127.0.0.1:5000", "/ws/feed", 403),
        '127.0.0.1:5000 - "WebSocket /ws/feed" 403',
        f'{WITHHELD} - "WebSocket {WITHHELD}" {WITHHELD}',
    ),
}
"""Server forms: format string, arguments, skeleton, every argument hidden."""


@pytest.mark.parametrize(
    ("message", "arguments", "expected"),
    [
        pytest.param(message, arguments, expected, id=kind)
        for kind, (message, arguments, expected, _) in _SERVER_FORMS.items()
    ],
)
def test_server_form_with_its_arity_prints_its_skeleton(
    message: str,
    arguments: tuple[object, ...],
    expected: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A server form with exactly its number of arguments is printed."""
    assert_that(SERVER_FORMS[message][0]).is_equal_to(len(arguments))

    printed = _printed(caplog, "uvicorn.error", message, *arguments)

    assert_that(printed).is_equal_to(expected)


@pytest.mark.parametrize(
    ("message", "arguments", "hidden"),
    [
        pytest.param(message, arguments, hidden, id=kind)
        for kind, (message, arguments, _, hidden) in _SERVER_FORMS.items()
    ],
)
@pytest.mark.parametrize("too_many", [False, True], ids=["one-too-few", "one-too-many"])
def test_server_form_with_another_arity_hides_every_argument(
    message: str,
    arguments: tuple[object, ...],
    hidden: str,
    too_many: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A server form with another number of arguments prints each as withheld."""
    assert_that(SERVER_FORMS[message][0]).is_equal_to(len(arguments))
    passed = (*arguments, _EXTRA) if too_many else arguments[:-1]

    printed = _printed(caplog, "uvicorn.error", message, *passed)

    assert_that(printed).is_equal_to(hidden)


def _padded(head: str, length: int, fill: str, tail: str = "") -> str:
    """Return ``head``, ``fill`` repeated and ``tail``: ``length`` characters."""
    filler = length - len(head) - len(tail)
    assert_that(filler).is_positive()
    return f"{head}{fill * filler}{tail}"


_LENGTHS = [
    pytest.param(MAX_PREFORMATTED, "#", True, id="at-bound"),
    pytest.param(MAX_PREFORMATTED, _NON_ASCII, True, id="at-bound-non-ascii"),
    pytest.param(MAX_PREFORMATTED + 1, "#", False, id="one-over"),
]
"""Pre-formatted text lengths: length, filler character, whether it prints."""


@pytest.mark.parametrize(("length", "fill", "shown"), _LENGTHS)
def test_spotify_line_is_bounded(
    length: int,
    fill: str,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Spotify SDK line one character past the bound is withheld."""
    line = _padded(_SPOTIFY_HEAD, length, fill)

    printed = _printed(caplog, "spotipy.client", line)

    assert_that(printed).is_equal_to(
        _SPOTIFY_SKELETON if shown else _unrecognised(length),
    )


_CANONICAL_HEAD = (
    "GET\n/artifacts/episode-1\n\nhost:storage.example.test\n"
    "x-amz-date:20261116T000000Z\n"
)
"""Method, path, empty query and two headers; the filler is one more line."""

_CANONICAL_TAIL = "\n\nhost;x-amz-date\nUNSIGNED-PAYLOAD"
"""The blank line, the signed header names and the payload hash."""

_CANONICAL_SKELETON = (
    "CanonicalRequest:\nGET /artifacts/episode-1 headers={'host': "
    "'storage.example.test', 'x-amz-date': '20261116T000000Z'} "
    "signed_headers=2 payload=UNSIGNED-PAYLOAD"
)


@pytest.mark.parametrize(("length", "fill", "shown"), _LENGTHS)
def test_canonical_request_is_bounded(
    length: int,
    fill: str,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A canonical request one character past the bound is withheld."""
    text = _padded(_CANONICAL_HEAD, length, fill, _CANONICAL_TAIL)

    printed = _printed(caplog, "botocore.auth", "CanonicalRequest:\n%s", text)

    assert_that(printed).is_equal_to(
        _CANONICAL_SKELETON if shown else f"CanonicalRequest:\n{WITHHELD}",
    )


_STRING_TO_SIGN_HEAD = (
    "AWS4-HMAC-SHA256\n20261116T000000Z\n20261116/us-east-1/s3/aws4_request\n"
)
"""The first three lines; the filler is the fourth, withheld on its own."""


@pytest.mark.parametrize(("length", "fill", "shown"), _LENGTHS)
def test_string_to_sign_is_bounded(
    length: int,
    fill: str,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A string to sign one character past the bound is withheld whole."""
    text = _padded(_STRING_TO_SIGN_HEAD, length, fill)

    printed = _printed(caplog, "botocore.auth", "StringToSign:\n%s", text)

    assert_that(printed).is_equal_to(
        (
            f"StringToSign:\n{_STRING_TO_SIGN_HEAD}{WITHHELD}"
            if shown
            else f"StringToSign:\n{WITHHELD}"
        ),
    )


_LONGEST_HOST = ".".join(("h" * 63, "h" * 63, "h" * 63, "h" * 61))
"""A 253-character host name, the longest a trace line's address may carry."""


def test_trace_line_with_the_longest_address_prints_it_whole(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The part of a trace line that is parsed covers the longest address."""
    line = (
        f"connect_tcp.started host='{_LONGEST_HOST}' port=65535 timeout=5.0 "
        "local_address=None socket_options=None"
    )

    printed = _printed(caplog, "httpcore.connection", line)

    assert_that(len(_LONGEST_HOST)).is_equal_to(253)
    assert_that(printed).is_equal_to(
        f"connect_tcp.started host='{_LONGEST_HOST}' port=65535",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("a" * MAX_HEADER_VALUE, "a" * MAX_HEADER_VALUE, id="text-at"),
        pytest.param("a" * (MAX_HEADER_VALUE + 1), WITHHELD, id="text-over"),
        pytest.param(b"a" * MAX_HEADER_VALUE, "a" * MAX_HEADER_VALUE, id="bytes-at"),
        pytest.param(b"a" * (MAX_HEADER_VALUE + 1), WITHHELD, id="bytes-over"),
    ],
)
def test_header_value_is_bounded(value: str | bytes, expected: str) -> None:
    """A header value one character past the bound is withheld."""
    assert_that(header_value(value)).is_equal_to(expected)


_REQUEST_LINE = '%s://%s:%s "%s %s %s" %s %s'
"""The connection pool's request line; its status and length are numbers."""

_ARABIC_VERSION = "HTTP/\N{ARABIC-INDIC DIGIT ONE}.\N{ARABIC-INDIC DIGIT ONE}"
"""An HTTP version the version pattern accepts, with two non-ASCII digits."""


def _request_line(version: str, length: int) -> tuple[object, ...]:
    """Return request-line arguments whose skeleton is ``length`` characters.

    The status and length fields carry the digits, split between two
    integers so that each stays within the interpreter's default limit on
    converting integers to text.
    """
    head = ("https", "storage.example.test", 443, "GET", "/artifacts/1", version)
    fixed = len(_REQUEST_LINE % (*head, "", ""))
    digits = length - fixed
    first, second = digits // 2, digits - digits // 2
    limit = sys.get_int_max_str_digits()
    assert_that(limit == 0 or second <= limit).is_true()
    return (*head, 10 ** (first - 1), 10 ** (second - 1))


@pytest.mark.parametrize(
    "version",
    [
        pytest.param("HTTP/1.1", id="ascii"),
        pytest.param(_ARABIC_VERSION, id="non-ascii"),
    ],
)
def test_skeleton_at_the_output_bound_is_printed(
    version: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A skeleton exactly as long as the bound, in characters, is printed."""
    arguments = _request_line(version, MAX_SKELETON)

    printed = _printed(caplog, "urllib3.connectionpool", _REQUEST_LINE, *arguments)

    assert_that(len(printed)).is_equal_to(MAX_SKELETON)
    assert_that(printed).is_equal_to(_REQUEST_LINE % arguments)


def test_skeleton_one_past_the_output_bound_is_withheld(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A skeleton one character past the bound withholds the record."""
    arguments = _request_line("HTTP/1.1", MAX_SKELETON + 1)

    printed = _printed(caplog, "urllib3.connectionpool", _REQUEST_LINE, *arguments)

    assert_that(printed).is_equal_to(
        "[record withheld: skeleton exceeds the output bound, "
        f"{len(_REQUEST_LINE)} characters]",
    )
