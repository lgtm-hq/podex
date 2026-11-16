"""Each size and shape bound of the third-party log filter, at and one past it.

Every test asserts on what a record prints, never on how long it takes: an
input exactly at a bound prints its normal form, and an input one unit past
it prints the withheld form the bound specifies. The bounds are imported
from the modules that define them, so a deliberate change of value moves the
inputs with it, while removing a check lets the one-past input print.
"""

import logging
import sys
from http import HTTPStatus

import pytest
from assertpy import assert_that

from podex import sdk_log_boundary, sdk_log_rules
from podex.sdk_log_boundary import MAX_SKELETON, _apply_form
from podex.sdk_log_forms import FORMS, MAX_PREFORMATTED, SERVER_FORMS
from podex.sdk_log_rules import MAX_HEADER_VALUE, WITHHELD, Rendered, header_value
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


def _header_values(limit: int) -> list[tuple[object, str]]:
    """Return header values at and one past ``limit``, with what each prints.

    Text and bytes of ``limit`` characters and one more; the largest integer
    whose decimal text is ``limit`` digits (all nines) and the next one; and
    the same for a negative integer, whose minus sign takes one character.
    """
    nines = 10**limit - 1
    negative = -(nines // 10)
    return [
        ("a" * limit, "a" * limit),
        ("a" * (limit + 1), WITHHELD),
        (b"a" * limit, "a" * limit),
        (b"a" * (limit + 1), WITHHELD),
        (nines, "9" * limit),
        (nines + 1, WITHHELD),
        (negative, "-" + "9" * (limit - 1)),
        (negative - 1, WITHHELD),
    ]


_HEADER_VALUE_KINDS = (
    "text-at",
    "text-over",
    "bytes-at",
    "bytes-over",
    "int-at",
    "int-over",
    "negative-at",
    "negative-over",
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(value, expected, id=kind)
        for kind, (value, expected) in zip(
            _HEADER_VALUE_KINDS,
            _header_values(MAX_HEADER_VALUE),
            strict=True,
        )
    ],
)
def test_header_value_is_bounded(value: object, expected: str) -> None:
    """A header value one character past the bound is withheld.

    :data:`MAX_HEADER_VALUE` is the only bound: the value pattern has no
    length of its own, text and bytes share one length check made before
    they are decoded or matched, and an integer is measured by magnitude
    with its minus sign counted. Removing any one of these checks lets a
    one-over case here print, and checking one character short makes an
    at-bound case withheld.
    """
    assert_that(header_value(value)).is_equal_to(expected)


@pytest.mark.parametrize(
    "limit",
    [MAX_HEADER_VALUE // 2, MAX_HEADER_VALUE * 2],
    ids=["lowered", "raised"],
)
def test_header_value_follows_the_one_limit(
    limit: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text, bytes and integers all follow the constant when it moves.

    A second copy of the bound, in the value pattern for example, would keep
    the old limit for some kind of value once the constant is raised.
    """
    monkeypatch.setattr(sdk_log_rules, "MAX_HEADER_VALUE", limit)
    cases = _header_values(limit)

    printed = [header_value(value) for value, _ in cases]

    assert_that(printed).is_equal_to([expected for _, expected in cases])


@pytest.mark.parametrize(
    "value",
    [10**100_000, -(10**100_000)],
    ids=["positive", "negative"],
)
def test_huge_integer_header_value_is_withheld(
    value: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An integer past the interpreter's text limit is withheld, not an error.

    Converting it to text would raise, so the record would print only the
    boundary's failure placeholder; it is measured without that conversion.
    """
    limit = sys.get_int_max_str_digits()
    assert_that(limit == 0 or limit < 100_000).is_true()

    printed = _printed(
        caplog,
        "botocore.parsers",
        "Response headers: %r",
        {"content-length": value},
    )

    assert_that(header_value(value)).is_equal_to(WITHHELD)
    assert_that(printed).is_equal_to(
        f"Response headers: {{'content-length': '{WITHHELD}'}}",
    )


@pytest.mark.parametrize(
    "value",
    [True, False, HTTPStatus.OK],
    ids=["true", "false", "int-subclass"],
)
def test_header_value_of_a_bool_or_int_subclass_is_withheld(value: int) -> None:
    """Only an exact ``int`` prints as a number; a bool or subclass is withheld."""
    assert_that(header_value(value)).is_equal_to(WITHHELD)


_CHOSEN = "k1-chosen-text"
"""Text a value's own methods would print instead of what was validated."""


class _ChosenText(str):
    """Text that validates as ``a`` but prints other text when represented."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Return text of the subclass's choosing."""
        return _CHOSEN


class _ChosenBytes(bytes):
    """Bytes that are ``a`` but decode to text of the subclass's choosing."""

    __slots__ = ()

    def decode(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        """Return text of the subclass's choosing, whatever the encoding."""
        del encoding, errors
        return _CHOSEN


@pytest.mark.parametrize(
    "value",
    [
        _ChosenText("a"),
        _ChosenBytes(b"a"),
        bytearray(b"a"),
        memoryview(b"a"),
    ],
    ids=["str-subclass", "bytes-subclass", "bytearray", "memoryview"],
)
def test_header_value_of_a_text_or_bytes_lookalike_is_withheld(
    value: object,
) -> None:
    """Only exact ``str`` and ``bytes`` print; a subclass or buffer is withheld.

    Each value is one ASCII letter, so it would validate and print if its
    type were admitted. A subclass could then print text of its own choosing
    through its ``__repr__`` or ``decode``.
    """
    assert_that(header_value(value)).is_equal_to(WITHHELD)


def test_header_value_text_subclass_cannot_choose_the_printed_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A text subclass in an allowlisted header prints as withheld."""
    printed = _printed(
        caplog,
        "botocore.parsers",
        "Response headers: %r",
        {"content-type": _ChosenText("a")},
    )

    assert_that(printed).is_equal_to(
        f"Response headers: {{'content-type': '{WITHHELD}'}}",
    )
    assert_that(printed).does_not_contain(_CHOSEN)


_REQUEST_LINE = '%s://%s:%s "%s %s %s" %s %s'
"""The connection pool's request line; its status and length are numbers."""

_ARABIC_VERSION = "HTTP/\N{ARABIC-INDIC DIGIT ONE}.\N{ARABIC-INDIC DIGIT ONE}"
"""An HTTP version with two non-ASCII digits, which the version pattern
withholds: validated parts are ASCII, so a skeleton is ASCII text."""


def _request_line(version: str) -> tuple[object, ...]:
    """Return request-line arguments with ``version``, all of them valid."""
    return (
        "https",
        "storage.example.test",
        443,
        "GET",
        "/artifacts/1",
        version,
        200,
        5,
    )


def _bound_at(length: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the output bound to ``length`` characters.

    Every part of a known form is bounded, and the longest skeleton a known
    form builds is shorter than :data:`MAX_SKELETON`, so a logged record
    reaches the bound only once it is set below that length. The bound at
    its own value is applied in :func:`test_output_bound_counts_characters_not_bytes`.
    """
    assert_that(length).is_less_than(MAX_SKELETON)
    monkeypatch.setattr(sdk_log_boundary, "MAX_SKELETON", length)


@pytest.mark.parametrize(
    ("version", "shown"),
    [
        pytest.param("HTTP/1.1", "HTTP/1.1", id="ascii"),
        pytest.param(_ARABIC_VERSION, WITHHELD, id="non-ascii-version-withheld"),
    ],
)
def test_skeleton_at_the_output_bound_is_printed(
    version: str,
    shown: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skeleton exactly as long as the bound, in characters, is printed.

    The bound applies to the skeleton, not to the record's input: a version
    with non-ASCII digits is withheld, and the skeleton printing
    ``[withheld]`` in its place is still printed at exactly the bound.
    """
    arguments = _request_line(shown)
    logged_arguments = (*arguments[:5], version, *arguments[6:])
    _bound_at(len(_REQUEST_LINE % arguments), monkeypatch)

    printed = _printed(
        caplog,
        "urllib3.connectionpool",
        _REQUEST_LINE,
        *logged_arguments,
    )

    assert_that(len(printed)).is_equal_to(sdk_log_boundary.MAX_SKELETON)
    assert_that(printed).is_equal_to(_REQUEST_LINE % arguments)


def test_skeleton_one_past_the_output_bound_is_withheld(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skeleton one character past the bound withholds the record."""
    arguments = _request_line("HTTP/1.1")
    _bound_at(len(_REQUEST_LINE % arguments) - 1, monkeypatch)

    printed = _printed(caplog, "urllib3.connectionpool", _REQUEST_LINE, *arguments)

    assert_that(printed).is_equal_to(
        "[record withheld: skeleton exceeds the output bound, "
        f"{len(_REQUEST_LINE)} characters]",
    )


_ORIGINAL_MESSAGE = "k1-original message"
"""The message of the record a skeleton is applied to."""


@pytest.mark.parametrize(
    ("length", "shown"),
    [(MAX_SKELETON, True), (MAX_SKELETON + 1, False)],
    ids=["at-bound", "one-over"],
)
@pytest.mark.parametrize(
    "formatted",
    [False, True],
    ids=["pre-formatted", "with-argument"],
)
def test_output_bound_counts_characters_not_bytes(
    length: int,
    shown: bool,
    formatted: bool,
) -> None:
    """A non-ASCII skeleton of exactly the bound, in characters, is printed.

    Validated parts are ASCII, so no record reaches a non-ASCII skeleton
    through a known form; the bound is applied here to a skeleton built
    directly, of characters that take two bytes each in UTF-8.
    """
    skeleton = _NON_ASCII * length
    form: tuple[str, tuple[object, ...]] = (
        ("%s", (Rendered(skeleton),)) if formatted else (skeleton, ())
    )
    record = logging.LogRecord(
        "botocore.auth",
        logging.DEBUG,
        __file__,
        1,
        _ORIGINAL_MESSAGE,
        (),
        None,
    )

    _apply_form(record, form)

    assert_that(record.getMessage()).is_equal_to(
        (
            skeleton
            if shown
            else "[record withheld: skeleton exceeds the output bound, "
            f"{len(_ORIGINAL_MESSAGE)} characters]"
        ),
    )
