"""Validators that build third-party SDK log skeletons.

A third-party record is printed only as a skeleton built from parts that are
safe by construction (:mod:`podex.sdk_log_forms`). This module validates
those parts; anything that does not validate is printed as ``[withheld]``:

- :func:`method`: an HTTP method from a fixed set.
- :func:`number` and :func:`status`: values that parse as numbers, each
  within the printed length of what its position carries.
- :func:`url_skeleton`: scheme, host, port and path, each validated;
  userinfo and fragment dropped; query names kept if they match a
  conservative pattern, credential-named values masked, other values kept
  only if short and conservative.
- :func:`headers_skeleton`: allowlisted header names only (credential-named
  names are never allowlisted, whatever the allowlist says), each value
  validated; every other header is counted, not named.
- :func:`class_name`: an exception class name validated as an identifier.
- :func:`reason_phrase`: the standard phrase for a status code.

Every builder is bounded: a fixed number of headers and query fields, a
fixed value length, a fixed URL length and a fixed printed length for every
number. Text that is case-mapped or stripped before it is compared must be
ASCII first: Unicode case rules map some other characters to ASCII letters
(the Kelvin sign lowercases to ``k``, the long s uppercases to ``S``).
"""

import contextlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sized
from email.message import Message
from http import HTTPStatus
from itertools import islice
from typing import TypeGuard
from urllib.parse import unquote_plus, urlsplit

import httpx
import httpx2

from podex.redaction import REDACTED, SecretRedactor

SAFE_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "accept-ranges",
        "age",
        "anthropic-version",
        "cache-control",
        "connection",
        "content-encoding",
        "content-language",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expect",
        "host",
        "last-modified",
        "request-id",
        "retry-after",
        "server",
        "transfer-encoding",
        "user-agent",
        "vary",
        "via",
        "x-amz-content-sha256",
        "x-amz-date",
        "x-amz-decoded-content-length",
        "x-amz-request-id",
        "x-amz-sdk-checksum-algorithm",
        "x-amz-trailer",
        "x-request-id",
        "x-should-retry",
    },
)
"""Header names that may be printed, with their validated values."""

SAFE_HEADER_PREFIXES = ("anthropic-ratelimit-", "x-ratelimit-")
"""Rate-limit header families, printed for diagnosing throttling."""

CREDENTIAL_KEYS = frozenset(
    {
        "accesskeyid",
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "awsaccesskeyid",
        "awssecretaccesskey",
        "awssessiontoken",
        "clientsecret",
        "code",
        "codeverifier",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "jwt",
        "key",
        "passwd",
        "password",
        "proxyauthorization",
        "pwd",
        "refreshtoken",
        "secret",
        "secretaccesskey",
        "securitytoken",
        "session",
        "sessiontoken",
        "setcookie",
        "sig",
        "signature",
        "sslpassword",
        "token",
        "xamzcredential",
        "xamzsecuritytoken",
        "xamzsignature",
        "xapikey",
        "xgoogapikey",
        "xgoogcredential",
        "xgoogsignature",
    },
)
"""Credential names, compared lowercased with every separator removed."""

COMPACT_CREDENTIAL_SUFFIXES = (
    "accesskey",
    "apikey",
    "credential",
    "credentials",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "sessionkey",
    "signature",
    "token",
)
"""Endings of a separator-free name that mark a credential (``mytoken``)."""

CREDENTIAL_SUFFIXES = ("_auth", "_key", "_secret", "_token")
"""Endings of a snake-case name that mark a credential (``x_api_key``)."""

METHODS = frozenset({"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"})
"""HTTP methods printed as they are."""

WITHHELD = "[withheld]"
MESSAGE_WITHHELD = "[message withheld]"

MAX_HEADERS = 16
"""Allowlisted headers printed per container; the rest are counted."""

MAX_HEADER_VALUE = 128
"""Longest header value printed; a longer one is withheld."""

MAX_QUERY_FIELDS = 16
"""Query fields printed per URL; the rest are counted."""

MAX_QUERY_VALUE = 64
"""Longest query value printed; a longer one is withheld."""

MAX_URL = 2_048
"""Longest URL parsed; a longer one is withheld."""

MAX_ITEMS = 1_000
"""Header entries read from a container; past it, they are only counted."""

MAX_STATUS_TEXT = 3
"""Longest status code printed where a form takes any number as its status.

The HTTP clients' parsers accept three digits only: ``http.client`` rejects
a status outside 100 to 999, and the ``h11`` parser requires three digits.
"""

MAX_PORT_TEXT = 5
"""Longest port printed: a TCP port is at most 65535."""

MAX_COUNT_TEXT = 20
"""Longest integer part of any other number printed.

Byte counts, connection and retry counts, pool sizes and delays: the
libraries hold them as machine-sized integers or as floats of seconds or
minutes, and the widest 64-bit integer (``18446744073709551615`` or
``-9223372036854775808``) is 20 characters.
"""

MAX_IDENTIFIER_TEXT = 512
"""Longest text matched against an identifier pattern; longer is withheld."""

_MAX_NAME = 256
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_WORD_SPLIT_RE = re.compile(r"[\s.\-]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SAFE_PREFIX_NAME_RE = re.compile(r"[a-z0-9\-]{1,64}")
_HEADER_VALUE_RE = re.compile(r"[A-Za-z0-9 _.:;,=/+*#()\-]*")
_QUERY_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_QUERY_VALUE_RE = re.compile(r"[A-Za-z0-9_.\-]{0,64}")
_HOST_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]")
_PATH_RE = re.compile(r"/?[A-Za-z0-9._~/\-]{0,512}")
_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,127}(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}){0,8}",
)
_NUMBER_TEXT_RE = re.compile(r"[0-9]{1,12}(?:\.[0-9]{1,6})?")
_ASCII_SPACE = "".join(char for char in map(chr, range(128)) if char.isspace())
_SCHEMES = frozenset({"http", "https"})
_DESCRIBED_TYPES = frozenset({str, bytes, bytearray, dict, list, tuple})

# URL userinfo and, once configured, the secret values of the loaded settings.
_REDACTOR = SecretRedactor()


def exact_str(value: object) -> TypeGuard[str]:
    """Return whether ``value`` is a ``str`` itself, not a subclass."""
    return type(value) in (str,)


def exact_int(value: object) -> TypeGuard[int]:
    """Return whether ``value`` is an ``int`` itself (not ``bool`` or a subclass)."""
    return type(value) in (int,)


def exact_float(value: object) -> TypeGuard[float]:
    """Return whether ``value`` is a ``float`` itself, not a subclass."""
    return type(value) in (float,)


def exact_bytes(value: object) -> TypeGuard[bytes]:
    """Return whether ``value`` is ``bytes`` itself, not a subclass."""
    return type(value) in (bytes,)


def exact_dict(value: object) -> TypeGuard[dict[object, object]]:
    """Return whether ``value`` is a ``dict`` itself, not a subclass."""
    return type(value) in (dict,)


def exact_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    """Return whether ``value`` is a ``tuple`` itself, not a subclass."""
    return type(value) in (tuple,)


class Rendered(str):
    """A skeleton part; ``%r`` prints it as ``%s`` does, without quotes."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Return the text itself."""
        return str(self)


def configure_secret_values(secrets: Iterable[str]) -> None:
    """Set the configured secret values replaced in finished skeletons.

    Args:
        secrets: Secret values of the loaded settings; empty values are ignored.
    """
    _REDACTOR.configure(secrets)


def redact_configured(text: str) -> str:
    """Replace URL userinfo and the configured secret values in ``text``.

    Args:
        text: A finished skeleton or rebuilt traceback.

    Returns:
        str: The text after the configured-value redactor.
    """
    redacted: str = _REDACTOR.redact(text)
    return redacted


def snake_case(name: str) -> str:
    """Return a name in snake case: ``X-Api-Key`` becomes ``x_api_key``."""
    split = _CAMEL_RE.sub("_", name[:_MAX_NAME]).lower()
    return "_".join(part for part in _WORD_SPLIT_RE.split(split) if part)


def _text_name(name: object) -> str | None:
    """Return a name as text, percent-decoded, or None if it is not a name."""
    if exact_bytes(name) and len(name) <= _MAX_NAME:
        name = name.decode("latin-1")
    if not exact_str(name) or len(name) > _MAX_NAME:
        return None
    if "%" in name or "+" in name:
        name = unquote_plus(name)
    return name


def is_credential_key(name: object) -> bool:
    """Return whether a field, header or query name holds a credential.

    The name is percent-decoded, then compared lowercased with separators
    removed (``ToKeN``, ``AWSAccessKeyId``, ``X-Amz-Signature``) against
    :data:`CREDENTIAL_KEYS` and :data:`COMPACT_CREDENTIAL_SUFFIXES`, and in
    snake case against :data:`CREDENTIAL_SUFFIXES`.

    Args:
        name: The name, as text or bytes.

    Returns:
        bool: True when the name marks a credential.
    """
    text = _text_name(name)
    if text is None:
        return False
    compact = _NON_ALNUM_RE.sub("", text.lower())
    if compact in CREDENTIAL_KEYS or compact.endswith(COMPACT_CREDENTIAL_SUFFIXES):
        return True
    return snake_case(text).endswith(CREDENTIAL_SUFFIXES)


def safe_header_name(name: object) -> str | None:
    """Return the printable form of an allowlisted header name, or None.

    A credential-named header is never allowlisted, whatever the allowlist
    or a prefix rule says (``X-Ratelimit-Token``).

    Args:
        name: The header name, as text or bytes.

    Returns:
        str | None: The lowercased name, or None if it may not be printed.
    """
    text = _text_name(name)
    if text is None or not text.isascii() or is_credential_key(text):
        return None
    lowered = text.strip(_ASCII_SPACE).lower()
    if lowered in SAFE_HEADERS:
        return lowered
    if lowered.startswith(SAFE_HEADER_PREFIXES) and _SAFE_PREFIX_NAME_RE.fullmatch(
        lowered,
    ):
        return lowered
    return None


def header_value(value: object) -> str:
    """Return an allowlisted header's value if it validates, else withheld.

    :data:`MAX_HEADER_VALUE` is the only bound: text and bytes are measured
    before they are decoded or matched, and an integer by magnitude, before
    it is converted to text.

    Args:
        value: The value, as text, bytes or an integer.

    Returns:
        str: The value, or ``[withheld]``.
    """
    if exact_int(value):
        fits = number_text_fits(value, MAX_HEADER_VALUE)
        return str(int(value)) if fits else WITHHELD
    raw: str | bytes
    if exact_bytes(value) or exact_str(value):
        raw = value
    else:
        return WITHHELD
    if len(raw) > MAX_HEADER_VALUE:
        return WITHHELD
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    return text if _HEADER_VALUE_RE.fullmatch(text) else WITHHELD


def number_text_fits(value: int | float, limit: int) -> bool:
    """Return whether a number's integer part prints in ``limit`` characters.

    Decided by comparing with a power of ten, never by building the text, so
    a number of any size is measured cheaply and an integer never meets the
    interpreter's limit on converting integers to text. A minus sign takes
    one character of the bound. Not-a-number and the infinities never fit.

    Args:
        value: An exact ``int`` or ``float``.
        limit: Most characters its integer part may print as.

    Returns:
        bool: True when ``str(int(value))`` is at most ``limit`` long.
    """
    digits = limit - 1 if value < 0 else limit
    bound: int = 10**digits
    return -bound < value < bound


def _size(value: object) -> int:
    """Return a container's size, or 0 if it has none or reading it fails."""
    size = 0
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = len(value)
    return size


def _header_items(headers: object) -> Iterator[tuple[object, object]]:
    """Yield a header container's name and value pairs, lazily.

    Only the known container types are read: the HTTP clients' header
    objects, mappings, the standard library's message headers, and lists or
    tuples of pairs.
    """
    pairs: Iterable[object] = ()
    if isinstance(headers, httpx.Headers | httpx2.Headers):
        pairs = headers.multi_items()
    elif isinstance(headers, Mapping | Message):
        pairs = headers.items()
    elif isinstance(headers, list | tuple) and type(headers) in (list, tuple):
        pairs = headers
    for pair in islice(pairs, MAX_ITEMS):
        if (
            isinstance(pair, list | tuple)
            and type(pair) in (list, tuple)
            and len(pair) == 2
        ):
            yield pair[0], pair[1]


def headers_skeleton(headers: object) -> str:
    """Print allowlisted headers with validated values; count the others.

    At most :data:`MAX_HEADERS` allowlisted headers are printed and at most
    :data:`MAX_ITEMS` entries are read; a container larger than that is only
    counted.

    Args:
        headers: A header mapping, header object or sequence of pairs.

    Returns:
        str: For example ``{'content-type': 'application/json'} (+2 other
            headers)``.
    """
    size = _size(headers)
    shown: dict[str, str] = {}
    read = 0
    if size <= MAX_ITEMS:
        for name, value in _header_items(headers):
            read += 1
            printable = safe_header_name(name)
            if printable is not None:
                shown[printable] = header_value(value)
                if len(shown) >= MAX_HEADERS:
                    break
    others = max(size, read) - len(shown)
    suffix = f" (+{others} other headers)" if others else ""
    return f"{shown}{suffix}"


def _query_field(field: str) -> str:
    """Print one query field: validated name, masked or validated value."""
    raw_name, equals, raw_value = field.partition("=")
    name = unquote_plus(raw_name) if len(raw_name) <= _MAX_NAME else ""
    if not _QUERY_NAME_RE.fullmatch(name):
        return WITHHELD
    if not equals:
        return name
    if is_credential_key(name):
        return f"{name}={REDACTED}"
    if not raw_value:
        return f"{name}="
    value = unquote_plus(raw_value) if len(raw_value) <= MAX_QUERY_VALUE * 3 else ""
    shown = value if value and _QUERY_VALUE_RE.fullmatch(value) else WITHHELD
    return f"{name}={shown}"


def _query_skeleton(query: str) -> str:
    """Print a query string's fields, bounded; count the rest."""
    fields = query.split("&", MAX_QUERY_FIELDS)
    printed = [_query_field(field) for field in fields[:MAX_QUERY_FIELDS]]
    rest = fields[MAX_QUERY_FIELDS:]
    if rest:
        printed.append(f"[+{rest[0].count('&') + 1} more fields]")
    return "&".join(printed)


def url_skeleton(url: object) -> str:
    """Print a URL or request target from validated parts only.

    Scheme (``http``/``https``), host, port and path are kept if they
    validate; userinfo and fragment are dropped; query fields are printed by
    validated name, credential values masked and other values kept only if
    short and conservative.

    Args:
        url: A URL, as text or a URL object.

    Returns:
        str: For example ``https://api.example.test/v1?token=[redacted]&page=2``.
    """
    if exact_str(url):
        text = str(url)
    elif isinstance(url, httpx.URL | httpx2.URL):
        text = str(url)
    else:
        return WITHHELD
    if not exact_str(text) or len(text) > MAX_URL:
        return WITHHELD
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return WITHHELD
    authority = ""
    if parts.scheme or parts.netloc:
        if parts.scheme not in _SCHEMES:
            return WITHHELD
        address = _host_and_port(parts.netloc, parts.hostname, port)
        authority = f"{parts.scheme}://{address}"
    path = parts.path if _PATH_RE.fullmatch(parts.path) else f"/{WITHHELD}"
    query = f"?{_query_skeleton(parts.query)}" if parts.query else ""
    return f"{authority}{path}{query}"


def _host_and_port(netloc: str, hostname: str | None, port: int | None) -> str:
    """Print a URL's validated host and port; withheld unless written in ASCII.

    The parser lowercases the host by Unicode rules, so the host and port
    are checked as written: the part of the authority after the userinfo,
    split off as the parser splits it.
    """
    if not netloc.rpartition("@")[2].isascii():
        return WITHHELD
    host = hostname or ""
    shown_host = host if _HOST_RE.fullmatch(host) else WITHHELD
    return f"{shown_host}{f':{port}' if port else ''}"


def method(value: object) -> str:
    """Return an HTTP method from the fixed set, uppercased, else withheld.

    Args:
        value: The method, as text or bytes.

    Returns:
        str: For example ``POST``.
    """
    if exact_bytes(value) and len(value) <= 16:
        value = value.decode("latin-1")
    if (
        exact_str(value)
        and len(value) <= 16
        and value.isascii()
        and value.upper() in METHODS
    ):
        return str.upper(value)
    return WITHHELD


def number(value: object, *, limit: int) -> int | float | None:
    """Return a value that is, or parses as, a number; None otherwise.

    A number whose integer part would print longer than ``limit``
    characters, or a float that is not finite, is None too; it is measured
    before any conversion to text (:func:`number_text_fits`).

    Args:
        value: An int, float or numeric text.
        limit: Most characters the integer part may print as.

    Returns:
        int | float | None: The number.
    """
    checked: int | float | None = None
    if exact_int(value):
        checked = int(value)
    elif exact_float(value):
        checked = float(value)
    elif exact_str(value) and len(value) <= 32:
        text = value.strip(_ASCII_SPACE)
        if _NUMBER_TEXT_RE.fullmatch(text):
            checked = float(text) if "." in text else int(text)
    if checked is None or not number_text_fits(checked, limit):
        return None
    return checked


def status(value: object) -> int | None:
    """Return an HTTP status code, or None.

    Args:
        value: The status.

    Returns:
        int | None: A code from 100 to 599.
    """
    if exact_int(value) and 100 <= value <= 599:
        return int(value)
    return None


def reason_phrase(code: object) -> str:
    """Return the standard reason phrase for a status, or ``""``.

    Args:
        code: An HTTP status code.

    Returns:
        str: For example ``"Internal Server Error"`` for 500.
    """
    phrase = ""
    checked = status(code)
    if checked is not None:
        with contextlib.suppress(ValueError):
            phrase = HTTPStatus(checked).phrase
    return phrase


def identifier(value: object, pattern: re.Pattern[str]) -> str:
    """Return text that fully matches ``pattern``, else withheld.

    Args:
        value: The candidate text.
        pattern: A bounded, conservative pattern.

    Returns:
        str: The text, or ``[withheld]``.
    """
    if (
        exact_str(value)
        and len(value) <= MAX_IDENTIFIER_TEXT
        and pattern.fullmatch(value)
    ):
        return str(value)
    return WITHHELD


def qualified_name(kind: object) -> str:
    """Return a class's module-qualified name, validated as an identifier.

    Args:
        kind: A class.

    Returns:
        str: For example ``httpx2.ConnectError``, or ``[withheld]``.
    """
    if not isinstance(kind, type):
        return WITHHELD
    module = (
        kind.__dict__.get("__module__", "") if "__module__" in kind.__dict__ else ""
    )
    name = kind.__qualname__
    if not exact_str(name):
        return WITHHELD
    if exact_str(module) and module not in ("builtins", "__main__", ""):
        name = f"{module}.{name}"
    return identifier(name, _IDENTIFIER_RE)


def class_name(error: BaseException) -> str:
    """Return an exception's validated class name.

    Args:
        error: The exception.

    Returns:
        str: For example ``httpx2.ConnectError``.
    """
    return qualified_name(type(error))


def describe_exception(error: BaseException) -> str:
    """Describe an exception by validated class, without its message.

    Args:
        error: The exception.

    Returns:
        str: For example ``"httpx2.ConnectError: [message withheld]"``.
    """
    return f"{class_name(error)}: {MESSAGE_WITHHELD}"


def withheld(value: object) -> str:
    """Describe a withheld value by type and size.

    Args:
        value: The value.

    Returns:
        str: For example ``"[withheld bytes (71)]"``.
    """
    kind = type(value)
    if kind not in _DESCRIBED_TYPES:
        return "[withheld]"
    size = f" ({len(value)})" if isinstance(value, Sized) else ""
    return f"[withheld {kind.__name__}{size}]"
