"""Validated digits and number text are ASCII only.

Every pattern of the third-party log filter that validates digits accepts
the ASCII digits ``0`` to ``9`` and nothing else. A record whose validated
part carries a decimal digit of another script prints that part withheld,
or is withheld whole where the filter parses the line whole, while its
ASCII twin prints as before. Each case writes the digit at one position of
one pattern, so a pattern that accepted any decimal digit there would print
the non-ASCII form.
"""

import logging
import sys
from dataclasses import dataclass

import pytest
from assertpy import assert_that

from podex.sdk_log_rules import WITHHELD
from tests.sdk_log_support import cached_record_text, debug_logging

_DIGIT = "<digit>"
"""Where a case writes its digit, in the message or a text argument."""

_ZEROS = {
    "arabic-indic": 0x0660,
    "devanagari": 0x0966,
    "fullwidth": 0xFF10,
}
"""The zero of each non-ASCII decimal digit script used, by name."""


@dataclass(frozen=True)
class _Case:
    """One validated digit position, with what its record prints."""

    logger: str
    message: str
    arguments: tuple[object, ...]
    digit: str
    ascii_printed: str
    other_printed: str | None
    """What the non-ASCII form prints; None if the record is withheld whole."""


def _with_digit(value: object, digit: str) -> object:
    """Return ``value`` with its digit placeholder replaced, if it is text."""
    return value.replace(_DIGIT, digit) if isinstance(value, str) else value


def _printed(caplog: pytest.LogCaptureFixture, case: _Case, digit: str) -> str:
    """Log ``case`` with ``digit`` written in and return what it prints."""
    message = case.message.replace(_DIGIT, digit)
    arguments = tuple(_with_digit(argument, digit) for argument in case.arguments)
    with debug_logging(caplog):
        logging.getLogger(case.logger).info(message, *arguments)
    printed: str = caplog.records[-1].getMessage()
    return printed


_HTTP_REQUEST = 'HTTP Request: %s %s "%s %d %s"'
_STRING_TO_SIGN = "StringToSign:\n%s"
_SIGNING_HEAD = "AWS4-HMAC-SHA256\n"
_SIGNING_TIMESTAMP = "20261116T000000Z\n"
_SIGNING_SCOPE = "20261116/us-east-1/s3/aws4_request\n"
_SIGNING_HASH = "UNSIGNED-PAYLOAD"
_SPOTIFY_ERROR = (
    "HTTP Error for GET to https://api.spotify.com/v1/me with Params: {} "
    "returned 40<digit> due to k1-reason"
)

_CASES: dict[str, _Case] = {
    "version-major": _Case(
        "httpx",
        _HTTP_REQUEST,
        ("GET", "https://api.example.test/v1", "HTTP/<digit>.1", 200, "OK"),
        "1",
        'HTTP Request: GET https://api.example.test/v1 "HTTP/1.1 200 OK"',
        f'HTTP Request: GET https://api.example.test/v1 "{WITHHELD} 200 OK"',
    ),
    "version-minor": _Case(
        "httpx",
        _HTTP_REQUEST,
        ("GET", "https://api.example.test/v1", "HTTP/1.<digit>", 200, "OK"),
        "1",
        'HTTP Request: GET https://api.example.test/v1 "HTTP/1.1 200 OK"',
        f'HTTP Request: GET https://api.example.test/v1 "{WITHHELD} 200 OK"',
    ),
    "timestamp-date": _Case(
        "botocore.auth",
        _STRING_TO_SIGN,
        (f"{_SIGNING_HEAD}2026111<digit>T000000Z\n{_SIGNING_SCOPE}{_SIGNING_HASH}",),
        "6",
        f"StringToSign:\n{_SIGNING_HEAD}{_SIGNING_TIMESTAMP}"
        f"{_SIGNING_SCOPE}{_SIGNING_HASH}",
        f"StringToSign:\n{_SIGNING_HEAD}{WITHHELD}\n{_SIGNING_SCOPE}{_SIGNING_HASH}",
    ),
    "timestamp-time": _Case(
        "botocore.auth",
        _STRING_TO_SIGN,
        (f"{_SIGNING_HEAD}20261116T00000<digit>Z\n{_SIGNING_SCOPE}{_SIGNING_HASH}",),
        "0",
        f"StringToSign:\n{_SIGNING_HEAD}{_SIGNING_TIMESTAMP}"
        f"{_SIGNING_SCOPE}{_SIGNING_HASH}",
        f"StringToSign:\n{_SIGNING_HEAD}{WITHHELD}\n{_SIGNING_SCOPE}{_SIGNING_HASH}",
    ),
    "scope-date": _Case(
        "botocore.auth",
        _STRING_TO_SIGN,
        (
            f"{_SIGNING_HEAD}{_SIGNING_TIMESTAMP}"
            f"2026111<digit>/us-east-1/s3/aws4_request\n{_SIGNING_HASH}",
        ),
        "6",
        f"StringToSign:\n{_SIGNING_HEAD}{_SIGNING_TIMESTAMP}"
        f"{_SIGNING_SCOPE}{_SIGNING_HASH}",
        f"StringToSign:\n{_SIGNING_HEAD}{_SIGNING_TIMESTAMP}{WITHHELD}\n"
        f"{_SIGNING_HASH}",
    ),
    "spotify-status": _Case(
        "spotipy.client",
        _SPOTIFY_ERROR,
        (),
        "4",
        "HTTP Error for GET to https://api.spotify.com/v1/me returned 404 due to "
        "[provider message withheld]",
        None,
    ),
    "trace-port": _Case(
        "httpcore.connection",
        "connect_tcp.started host='api.example.test' port=44<digit> "
        "local_address=None",
        (),
        "3",
        "connect_tcp.started host='api.example.test' port=443",
        "connect_tcp.started",
    ),
    "trace-status": _Case(
        "httpcore.http11",
        "receive_response_headers.complete return_value=(b'HTTP/1.1', 20<digit>, "
        "[])",
        (),
        "0",
        "receive_response_headers.complete protocol=HTTP/1.1 status=200",
        "receive_response_headers.complete",
    ),
    "number-integer": _Case(
        "urllib3.connectionpool",
        "Starting new HTTPS connection (%d): %s:%s",
        (1, "api.example.test", "44<digit>"),
        "3",
        "Starting new HTTPS connection (1): api.example.test:443",
        f"Starting new HTTPS connection (1): api.example.test:{WITHHELD}",
    ),
    "number-fraction": _Case(
        "botocore.retries.standard",
        "Retry needed, retrying request after delay of: %s",
        ("0.<digit>",),
        "5",
        "Retry needed, retrying request after delay of: 0.5",
        f"Retry needed, retrying request after delay of: {WITHHELD}",
    ),
}
"""Each validated digit position: the case and the digit it writes."""


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=name) for name, case in _CASES.items()],
)
def test_ascii_digit_prints(case: _Case, caplog: pytest.LogCaptureFixture) -> None:
    """The ASCII form of each validated digit position prints."""
    printed = _printed(caplog, case, case.digit)

    assert_that(printed).is_equal_to(case.ascii_printed)


@pytest.mark.parametrize("zero", _ZEROS.values(), ids=list(_ZEROS))
@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=name) for name, case in _CASES.items()],
)
def test_non_ascii_digit_is_withheld(
    case: _Case,
    zero: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same digit in another script is withheld."""
    digit = chr(zero + int(case.digit))

    printed = _printed(caplog, case, digit)

    message = case.message.replace(_DIGIT, digit)
    assert_that(printed).is_equal_to(
        case.other_printed
        or f"[record withheld: unrecognised form, {len(message)} characters]",
    )
    assert_that(printed.isascii()).is_true()


@pytest.mark.parametrize(
    ("space", "shown"),
    [
        pytest.param(" \t", "5", id="ascii"),
        pytest.param("\N{IDEOGRAPHIC SPACE}", WITHHELD, id="ideographic"),
        pytest.param("\N{NO-BREAK SPACE}", WITHHELD, id="no-break"),
    ],
)
def test_number_text_is_padded_with_ascii_space_only(
    space: str,
    shown: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Number text may carry ASCII white space around it, and nothing else."""
    with debug_logging(caplog):
        logging.getLogger("botocore.retries.standard").info(
            "Max attempts of %s reached.",
            f"{space}5{space}",
        )

    assert_that(caplog.records[-1].getMessage()).is_equal_to(
        f"Max attempts of {shown} reached.",
    )


_STRIPPED = [char for char in map(chr, range(sys.maxunicode + 1)) if not char.strip()]
"""Every character ``str.strip()`` removes, found by asking the interpreter."""

_ASCII_STRIPPED = [char for char in _STRIPPED if char.isascii()]
_OTHER_STRIPPED = [char for char in _STRIPPED if not char.isascii()]

_ASCII_STRIPPED_COUNT = 10
"""Tab, line feed, vertical tab, form feed, carriage return, the four
information separators (``\\x1c`` to ``\\x1f``) and space."""

_OTHER_STRIPPED_COUNT = 19
"""Next line, no-break space, Ogham space mark, the eleven spaces from
U+2000 to U+200A, the line and paragraph separators, the narrow no-break
and medium mathematical spaces, and the ideographic space."""

_POSITIONS = {
    "left": "{space}5",
    "right": "5{space}",
    "both": "{space}5{space}",
}
"""Where the white space is put around the number text."""


def _space_cases(spaces: list[str]) -> list[object]:
    """Return a case for each white space character in each position."""
    return [
        pytest.param(position.format(space=space), id=f"U+{ord(space):04X}-{name}")
        for space in spaces
        for name, position in _POSITIONS.items()
    ]


def test_the_stripped_characters_are_the_expected_ones() -> None:
    """The interpreter strips the white space the cases below expect.

    If a new Unicode version adds white space, the counts change here
    first, and the new characters join the cases below automatically.
    """
    assert_that(_ASCII_STRIPPED).is_length(_ASCII_STRIPPED_COUNT)
    assert_that(_OTHER_STRIPPED).is_length(_OTHER_STRIPPED_COUNT)


def _logged_number(caplog: pytest.LogCaptureFixture, text: str) -> str:
    """Log ``text`` as a number argument and return what the record prints."""
    with debug_logging(caplog):
        logging.getLogger("botocore.retries.standard").info(
            "Max attempts of %s reached.",
            text,
        )
    printed: str = caplog.records[-1].getMessage()
    return printed


@pytest.mark.parametrize("text", _space_cases(_ASCII_STRIPPED))
def test_number_text_with_ascii_space_around_it_prints(
    text: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each ASCII white space character may pad number text on either side."""
    printed = _logged_number(caplog, text)

    assert_that(printed).is_equal_to("Max attempts of 5 reached.")


@pytest.mark.parametrize("text", _space_cases(_OTHER_STRIPPED))
def test_number_text_with_other_space_around_it_is_withheld(
    text: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each non-ASCII white space character, on either side, is withheld."""
    printed = _logged_number(caplog, text)

    assert_that(printed).is_equal_to(f"Max attempts of {WITHHELD} reached.")


_FRAME = '  File "/srv/app.py", line <digit>, in fetch'


@pytest.mark.parametrize(
    ("zero", "kept"),
    [
        pytest.param(ord("0"), True, id="ascii"),
        *(pytest.param(zero, False, id=name) for name, zero in _ZEROS.items()),
    ],
)
def test_stack_frame_line_number_is_ascii_digits_only(
    zero: int,
    kept: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stack frame line is kept only if its line number is ASCII digits."""
    frame = _FRAME.replace(_DIGIT, chr(zero + 7))
    stack = f"Stack (most recent call last):\n{frame}\n    fetch_source()"
    with cached_record_text("cached traceback", stack):
        with debug_logging(caplog):
            logging.getLogger("anthropic._base_client").debug("Encountered Exception")

    assert_that(caplog.records[-1].stack_info).is_equal_to(
        "Stack (most recent call last):"
        + ('\n  File "/srv/app.py", line 7, in fetch' if kept else ""),
    )
