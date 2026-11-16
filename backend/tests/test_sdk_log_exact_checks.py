"""Validated parts are exact: ASCII before case rules, exact types, bounded numbers.

Three rules of the third-party log filter, each checked on what a record
prints:

- Header names, methods and hosts are compared after case-mapping (and
  header names after stripping white space). Unicode case rules map a few
  other characters to ASCII letters (the Kelvin sign lowercases to ``k``,
  the long s uppercases to ``S``) and strip other white space, so each of
  these parts must be ASCII as written. Every character the interpreter maps
  or strips into ASCII is tried, found by asking the interpreter, and its
  ASCII twin prints as before.
- A text part is compared only once it is an exact ``str``: a subclass can
  claim to equal anything and then print text of its own choosing.
- Every number printed has a length bound, measured before it is converted
  to text: a status code three characters, a port five, any other number
  twenty in its integer part. A number past its bound, a float that is not
  finite, and an integer too large to convert to text print as withheld,
  never as the boundary's failure placeholder.
"""

import logging
import math
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from assertpy import assert_that

from podex.sdk_log_forms import FORMS, SERVER_FORMS
from podex.sdk_log_rules import (
    MAX_COUNT_TEXT,
    MAX_PORT_TEXT,
    MAX_STATUS_TEXT,
    METHODS,
    SAFE_HEADERS,
    WITHHELD,
)
from tests.sdk_log_support import assert_quick_record, debug_logging

_FAILED = "<unprintable"
"""The start of the boundary's placeholder when sanitising a record fails."""


def _printed(
    caplog: pytest.LogCaptureFixture,
    logger_name: str,
    message: str,
    *arguments: object,
) -> str:
    """Log one record as the application does and return what it prints."""
    with debug_logging(caplog):
        logging.getLogger(logger_name).warning(message, *arguments)
    printed: str = caplog.records[-1].getMessage()
    return printed


_NON_ASCII = [chr(code) for code in range(0x80, sys.maxunicode + 1)]
_LOWERED_TO_ASCII = [char for char in _NON_ASCII if char.lower().isascii()]
"""Every non-ASCII character whose lowercase form is ASCII."""
_UPPERED_TO_ASCII = [char for char in _NON_ASCII if char.upper().isascii()]
"""Every non-ASCII character whose uppercase form is ASCII."""
_STRIPPED = [char for char in _NON_ASCII if not char.strip()]
"""Every non-ASCII character ``str.strip()`` removes."""
_ASCII_STRIPPED = [chr(code) for code in range(128) if not chr(code).strip()]


def test_the_characters_mapped_into_ascii_are_the_expected_ones() -> None:
    """The interpreter maps the characters the cases below expect.

    If a new Unicode version maps more characters into ASCII, the lists
    change here first, and the new characters join the cases below.
    """
    assert_that(_LOWERED_TO_ASCII).is_equal_to(["\N{KELVIN SIGN}"])
    assert_that(_UPPERED_TO_ASCII).contains(
        "\N{LATIN SMALL LETTER DOTLESS I}",
        "\N{LATIN SMALL LETTER LONG S}",
        "\N{LATIN SMALL LIGATURE ST}",
        "\N{LATIN SMALL LIGATURE LONG S T}",
    )
    assert_that(_UPPERED_TO_ASCII).is_length(10)
    assert_that(_STRIPPED).is_length(19)


_HEADERS = "Response headers: %r"
_HEADERS_LOGGER = "botocore.parsers"
_PREFIXED_HEADER = "x-ratelimit-"
_NO_HEADER = "Response headers: {} (+1 other headers)"


def _header_printed(caplog: pytest.LogCaptureFixture, name: str | bytes) -> str:
    """Log a response header ``name`` with a valid value; return the line."""
    return _printed(caplog, _HEADERS_LOGGER, _HEADERS, {name: "1"})


def _lowered_header_cases() -> Iterator[object]:
    """Each allowlisted name with one letter written as a character lowering to it.

    Every allowlisted name holding the letter, and a rate-limit name under
    an allowlisted prefix.
    """
    for char in _LOWERED_TO_ASCII:
        letter = char.lower()
        names = sorted(name for name in SAFE_HEADERS if letter in name)
        names.append(f"{_PREFIXED_HEADER}{letter}")
        for name in names:
            written = name.replace(letter, char, 1)
            yield pytest.param(written, name, id=f"U+{ord(char):04X}-{name}")


@pytest.mark.parametrize(("written", "name"), list(_lowered_header_cases()))
def test_header_name_lowering_into_ascii_is_withheld(
    written: str,
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A header name with a character that lowercases into ASCII is not named."""
    del name
    printed = _header_printed(caplog, written)

    assert_that(printed).is_equal_to(_NO_HEADER)


@pytest.mark.parametrize(("written", "name"), list(_lowered_header_cases()))
def test_header_name_ascii_twin_prints(
    written: str,
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ASCII name, in either case, is printed lowercased."""
    del written
    for twin in (name, name.upper()):
        printed = _header_printed(caplog, twin)

        assert_that(printed).is_equal_to(f"Response headers: {{'{name}': '1'}}")


_PADDINGS = {
    "left": "{space}{name}",
    "right": "{name}{space}",
    "both": "{space}{name}{space}",
}


def _padded_names(spaces: list[str]) -> Iterator[object]:
    """``content-type`` padded with each space, on each side."""
    for space in spaces:
        for side, padding in _PADDINGS.items():
            written = padding.format(space=space, name="content-type")
            yield pytest.param(written, id=f"U+{ord(space):04X}-{side}")


@pytest.mark.parametrize("written", list(_padded_names(_STRIPPED)))
def test_header_name_padded_with_other_space_is_withheld(
    written: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A header name padded with non-ASCII white space is not named."""
    assert_that(_header_printed(caplog, written)).is_equal_to(_NO_HEADER)


@pytest.mark.parametrize("written", list(_padded_names(_ASCII_STRIPPED)))
def test_header_name_padded_with_ascii_space_prints(
    written: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ASCII white space around a header name is stripped, as before."""
    assert_that(_header_printed(caplog, written)).is_equal_to(
        "Response headers: {'content-type': '1'}",
    )


@pytest.mark.parametrize(
    ("written", "shown"),
    [
        *(
            pytest.param(
                f"{space}content-type".encode("latin-1"),
                None,
                id=f"bytes-U+{ord(space):04X}",
            )
            for space in _STRIPPED
            if ord(space) < 0x100
        ),
        pytest.param(b" content-type", "content-type", id="bytes-ascii"),
        pytest.param("x-ratelimit-%E2%84%AA", None, id="percent-kelvin"),
        pytest.param("x-ratelimit-%4B", "x-ratelimit-k", id="percent-ascii"),
    ],
)
def test_header_name_is_ascii_as_decoded(
    written: str | bytes,
    shown: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Names given as bytes or percent-encoded are checked once decoded."""
    printed = _header_printed(caplog, written)

    expected = _NO_HEADER if shown is None else f"Response headers: {{'{shown}': '1'}}"
    assert_that(printed).is_equal_to(expected)


_METHOD = "HTTP request method: %s"
_METHOD_LOGGER = "botocore.endpoint"


def _method_cases() -> Iterator[object]:
    """Each method with letters written as a character uppercasing to them."""
    for char in _UPPERED_TO_ASCII:
        letters = char.upper()
        for name in sorted(METHODS):
            start = name.find(letters)
            if start >= 0:
                written = f"{name[:start]}{char}{name[start + len(letters) :]}"
                yield pytest.param(written, name, id=f"U+{ord(char):04X}-{name}")


_METHOD_CASES = list(_method_cases())


def test_every_method_case_is_found() -> None:
    """Dotless i, long s and the two s-t ligatures each write a method."""
    assert_that(_METHOD_CASES).is_length(5)


@pytest.mark.parametrize(("written", "name"), _METHOD_CASES)
def test_method_uppercasing_into_ascii_is_withheld(
    written: str,
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A method with a character that uppercases into ASCII is withheld."""
    del name
    printed = _printed(caplog, _METHOD_LOGGER, _METHOD, written)

    assert_that(printed).is_equal_to(f"HTTP request method: {WITHHELD}")


@pytest.mark.parametrize(("written", "name"), _METHOD_CASES)
def test_method_ascii_twin_prints(
    written: str,
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ASCII method, in either case, prints uppercased."""
    del written
    for twin in (name, name.lower()):
        printed = _printed(caplog, _METHOD_LOGGER, _METHOD, twin)

        assert_that(printed).is_equal_to(f"HTTP request method: {name}")


_REQUEST = "Sending HTTP Request: %s %s"
_REQUEST_LOGGER = "anthropic._base_client"


def _host_cases() -> Iterator[object]:
    """A host with one letter written as a character lowering to it."""
    for char in _LOWERED_TO_ASCII:
        for port in ("", ":8443"):
            yield pytest.param(
                f"{char}.example.test{port}",
                f"{char.lower()}.example.test{port}",
                id=f"U+{ord(char):04X}{'-port' if port else ''}",
            )


@pytest.mark.parametrize(("written", "twin"), list(_host_cases()))
def test_host_lowering_into_ascii_is_withheld(
    written: str,
    twin: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A host with a character that lowercases into ASCII is withheld.

    Its port is withheld with it: the host and port are checked as written.
    """
    del twin
    printed = _printed(
        caplog,
        _REQUEST_LOGGER,
        _REQUEST,
        "GET",
        f"https://{written}/v1",
    )

    assert_that(printed).is_equal_to(f"Sending HTTP Request: GET https://{WITHHELD}/v1")


@pytest.mark.parametrize(("written", "twin"), list(_host_cases()))
def test_host_ascii_twin_prints(
    written: str,
    twin: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ASCII host, in either case, prints lowercased with its port."""
    del written
    for url in (f"https://{twin}/v1", f"https://{twin.upper()}/v1"):
        printed = _printed(caplog, _REQUEST_LOGGER, _REQUEST, "GET", url)

        assert_that(printed).is_equal_to(f"Sending HTTP Request: GET https://{twin}/v1")


def test_non_ascii_userinfo_does_not_withhold_the_host(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the host and port must be ASCII; the userinfo is dropped anyway."""
    printed = _printed(
        caplog,
        _REQUEST_LOGGER,
        _REQUEST,
        "GET",
        "https://reader\N{KELVIN SIGN}@api.example.test:8443/v1",
    )

    assert_that(printed).is_equal_to(
        "Sending HTTP Request: GET https://api.example.test:8443/v1",
    )


_CHOSEN = "k1-chosen-text"
"""Text an impostor prints if a validator hands it on instead of withholding it."""


class _Impostor(str):
    """Text that claims to equal anything and prints text of its own choosing."""

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        """Claim to equal anything."""
        del other
        return True

    def __ne__(self, other: object) -> bool:
        """Claim to differ from nothing."""
        del other
        return False

    def __hash__(self) -> int:
        """Hash as the text it holds."""
        return str.__hash__(self)

    def __str__(self) -> str:
        """Return the chosen text."""
        return _CHOSEN

    def __repr__(self) -> str:
        """Return the chosen text."""
        return _CHOSEN

    def __format__(self, spec: str) -> str:
        """Return the chosen text."""
        del spec
        return _CHOSEN


class _Text(str):
    """A plain text subclass, equal to its value only."""

    __slots__ = ()


_REQUEST_LINE = '%s://%s:%s "%s %s %s" %s %s'
_POOL_LOGGER = "urllib3.connectionpool"
_LINE_TAIL: tuple[object, ...] = (
    "api.example.test",
    443,
    "GET",
    "/v1",
    "HTTP/1.1",
    200,
    5,
)


@pytest.mark.parametrize(
    "scheme",
    [_Impostor(_CHOSEN), _Impostor("https"), _Text("https"), _Text("http")],
    ids=["impostor", "impostor-https", "subclass-https", "subclass-http"],
)
def test_request_line_scheme_subclass_is_withheld(
    scheme: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The scheme must be an exact ``str``, whatever it claims to equal."""
    printed = _printed(caplog, _POOL_LOGGER, _REQUEST_LINE, scheme, *_LINE_TAIL)

    assert_that(printed).is_equal_to(
        f'{WITHHELD}://api.example.test:443 "GET /v1 HTTP/1.1" 200 5',
    )


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_request_line_scheme_as_text_prints(
    scheme: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact-type twin prints."""
    printed = _printed(caplog, _POOL_LOGGER, _REQUEST_LINE, scheme, *_LINE_TAIL)

    assert_that(printed).is_equal_to(
        f'{scheme}://api.example.test:443 "GET /v1 HTTP/1.1" 200 5',
    )


@dataclass(frozen=True)
class _Slot:
    """One argument position of one known form, logged on its logger."""

    logger: str
    message: str
    arity: int
    position: int

    def printed(self, caplog: pytest.LogCaptureFixture, value: object) -> str:
        """Log the form with ``value`` here and no accepted value elsewhere."""
        arguments = tuple(
            value if index == self.position else object() for index in range(self.arity)
        )
        return _printed(caplog, self.logger, self.message, *arguments)


def _every_slot() -> Iterator[object]:
    """Each argument position of each known form, with its logger name."""
    forms = [
        *(
            (families[0], message, arity)
            for message, (families, arity, _) in FORMS.items()
        ),
        *(
            ("uvicorn.error", message, arity)
            for message, (arity, _) in SERVER_FORMS.items()
        ),
    ]
    for logger, message, arity in forms:
        for position in range(arity):
            yield pytest.param(
                _Slot(logger, message, arity, position),
                id=f"{logger}-{message[:40]}-{position}",
            )


_SLOTS = list(_every_slot())


@pytest.mark.parametrize("slot", _SLOTS)
def test_impostor_text_in_any_position_prints_nothing_of_its_own(
    slot: _Slot,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No part of any form hands on text that is not an exact ``str``."""
    printed = slot.printed(caplog, _Impostor(_CHOSEN))

    assert_that(printed).does_not_contain(_CHOSEN)
    assert_that(printed).does_not_contain(_FAILED)


_HUGE = 10**100_000
"""An integer far past the interpreter's limit on converting integers to text."""

_LONG_DIGITS_RE = re.compile(rf"[0-9]{{{MAX_COUNT_TEXT + 1},}}")


@pytest.mark.parametrize("sign", [1, -1], ids=["positive", "negative"])
@pytest.mark.parametrize("slot", _SLOTS)
def test_huge_integer_in_any_position_prints_as_withheld(
    slot: _Slot,
    sign: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A huge integer prints exactly as a value of no accepted type does.

    Never the failure placeholder, and never a run of digits longer than
    any number's bound.
    """
    unaccepted = slot.printed(caplog, object())

    printed = slot.printed(caplog, sign * _HUGE)

    assert_that(printed).is_equal_to(unaccepted)
    assert_that(printed).does_not_contain(_FAILED)
    assert_that(_LONG_DIGITS_RE.search(printed)).is_none()


_HUGE_RECORDS = {
    "count": ("anthropic._base_client", "%i retries left", (_HUGE,)),
    "status": ("anthropic._base_client", "Retrying due to status code %i", (_HUGE,)),
    "request-line": (
        _POOL_LOGGER,
        _REQUEST_LINE,
        ("https", _HUGE, _HUGE, "GET", "/v1", _HUGE, _HUGE, _HUGE),
    ),
    "identifier": ("anthropic._base_client", "request_id: %s", (_HUGE,)),
}
"""One record per kind of numeric part, each carrying the huge integer."""


@pytest.mark.parametrize(
    ("logger_name", "message", "arguments"),
    [pytest.param(*record, id=kind) for kind, record in _HUGE_RECORDS.items()],
)
def test_huge_integer_is_measured_in_bounded_time(
    logger_name: str,
    message: str,
    arguments: tuple[object, ...],
) -> None:
    """A huge integer costs about as much as an ordinary record to withhold."""
    assert_quick_record(
        logging.getLogger(logger_name), logging.INFO, message, *arguments
    )


def _bound_cases(limit: int) -> list[tuple[object, bool]]:
    """Numbers at and one past a printed length of ``limit``, as int and text."""
    largest = 10**limit - 1
    lowest = -(10 ** (limit - 1) - 1)
    return [
        (largest, True),
        (largest + 1, False),
        (lowest, True),
        (lowest - 1, False),
        (str(largest), True),
        (str(largest + 1), False),
    ]


_BOUND_IDS = [
    "int-at",
    "int-over",
    "negative-at",
    "negative-over",
    "text-at",
    "text-over",
]


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        pytest.param(value, shown, id=kind)
        for kind, (value, shown) in zip(
            _BOUND_IDS,
            _bound_cases(MAX_STATUS_TEXT),
            strict=True,
        )
    ],
)
def test_status_is_bounded(
    value: object,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A status prints up to three characters; one more is withheld.

    In the two forms that take any number as their status.
    """
    status = str(int(str(value))) if shown else WITHHELD

    retry = _printed(
        caplog,
        "anthropic._base_client",
        "Retrying due to status code %i",
        value,
    )
    line = _printed(
        caplog,
        _POOL_LOGGER,
        _REQUEST_LINE,
        "https",
        *_LINE_TAIL[:5],
        value,
        5,
    )

    assert_that(retry).is_equal_to(f"Retrying due to status code {status}")
    assert_that(line).is_equal_to(
        f'https://api.example.test:443 "GET /v1 HTTP/1.1" {status} 5',
    )


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        pytest.param(value, shown, id=kind)
        for kind, (value, shown) in zip(
            _BOUND_IDS,
            _bound_cases(MAX_PORT_TEXT),
            strict=True,
        )
    ],
)
def test_port_is_bounded(
    value: object,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A port prints up to five characters; one more is withheld."""
    printed = _printed(
        caplog,
        _POOL_LOGGER,
        "Starting new HTTPS connection (%d): %s:%s",
        1,
        "api.example.test",
        value,
    )

    port = str(int(str(value))) if shown else WITHHELD
    assert_that(printed).is_equal_to(
        f"Starting new HTTPS connection (1): api.example.test:{port}",
    )


@pytest.mark.parametrize(
    ("port", "shown"),
    [("99999", True), ("100000", False)],
    ids=["at", "over"],
)
def test_client_port_is_bounded(
    port: str,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The access line's client port follows the port bound."""
    printed = _printed(
        caplog,
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        f"127.0.0.1:{port}",
        "GET",
        "/v1",
        "1.1",
        200,
    )

    assert_that(printed).is_equal_to(
        f'127.0.0.1:{port if shown else WITHHELD} - "GET /v1 HTTP/1.1" 200',
    )


def _count_cases() -> list[object]:
    """Integers and floats at and one past the count bound, and non-finite floats."""
    largest = 10**MAX_COUNT_TEXT
    smallest = -(10 ** (MAX_COUNT_TEXT - 1))
    return [
        pytest.param(largest - 1, True, id="int-at"),
        pytest.param(largest, False, id="int-over"),
        pytest.param(smallest + 1, True, id="negative-at"),
        pytest.param(smallest, False, id="negative-over"),
        pytest.param(math.nextafter(float(largest), 0), True, id="float-at"),
        pytest.param(float(largest), False, id="float-over"),
        pytest.param(math.nextafter(float(smallest), 0), True, id="negative-float-at"),
        pytest.param(float(smallest), False, id="negative-float-over"),
        pytest.param(math.inf, False, id="inf"),
        pytest.param(-math.inf, False, id="negative-inf"),
        pytest.param(math.nan, False, id="nan"),
        pytest.param(sys.float_info.max, False, id="largest-float"),
    ]


_COUNT_FORMS: dict[str, tuple[str, str, tuple[object, ...]]] = {
    "text": ("botocore.retries.standard", "Max attempts of %s reached.", ()),
    "integer": ("anthropic._base_client", "%i retries left", ()),
    "fixed": (
        "anthropic._base_client",
        "Retrying request to %s in %f seconds",
        ("https://api.example.test/v1",),
    ),
}
"""The count bound under each kind of placeholder: ``%s``, ``%i`` and ``%f``.

Each form's logger, format string and the arguments before the number.
"""


@pytest.mark.parametrize("kind", list(_COUNT_FORMS))
@pytest.mark.parametrize(("value", "shown"), _count_cases())
def test_count_is_bounded(
    value: float,
    shown: bool,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Any other number prints up to twenty characters in its integer part.

    One more, an infinity or not-a-number is withheld under every kind of
    placeholder, where an infinity under ``%i`` used to fail the record.
    """
    logger_name, message, leading = _COUNT_FORMS[kind]

    printed = _printed(caplog, logger_name, message, *leading, value)

    withheld = message.replace("%f", "%s").replace("%i", "%s") % (*leading, WITHHELD)
    assert_that(printed).is_equal_to(
        message % (*leading, value) if shown else withheld,
    )


_PRODUCED = {
    "status-lowest": ("status", 100, True),
    "status-highest": ("status", 999, True),
    "status-wider": ("status", 1000, False),
    "port-highest": ("port", 65_535, True),
    "port-wider": ("port", 100_000, False),
    "count-unsigned-64": ("count", 2**64 - 1, True),
    "count-signed-64": ("count", -(2**63), True),
    "count-wider": ("count", 10**20, False),
    "count-negative-wider": ("count", -(10**19), False),
}
"""The widest values the libraries produce, written out rather than derived
from the bounds, and the nearest wider value of the same sign: a status of
up to three digits, a TCP port, and a 64-bit integer."""

_PRODUCED_FORMS = {
    "status": ("anthropic._base_client", "Retrying due to status code %i", ()),
    "port": (_POOL_LOGGER, "Starting new HTTPS connection (%d): %s:%s", (1, "h.test")),
    "count": ("botocore.retries.standard", "Max attempts of %s reached.", ()),
}


@pytest.mark.parametrize(
    ("kind", "value", "shown"),
    [pytest.param(*case, id=name) for name, case in _PRODUCED.items()],
)
def test_bounds_admit_what_the_libraries_produce_and_nothing_wider(
    kind: str,
    value: int,
    shown: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Each bound is exactly as wide as the values its position carries."""
    logger_name, message, leading = _PRODUCED_FORMS[kind]

    printed = _printed(caplog, logger_name, message, *leading, value)

    withheld = message.replace("%i", "%s") % (*leading, WITHHELD)
    assert_that(printed).is_equal_to(
        message % (*leading, value) if shown else withheld,
    )
