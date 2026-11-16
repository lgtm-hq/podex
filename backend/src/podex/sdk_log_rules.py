"""Validators that build third-party SDK log skeletons.

A third-party record is printed only as a skeleton built from parts that are
safe by construction (:mod:`podex.sdk_log_forms`). This module validates
those parts; anything that does not validate is printed as ``[withheld]``:

- :func:`method`: an HTTP method from a fixed set.
- :func:`number` and :func:`status`: values that parse as numbers.
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
fixed value length and a fixed URL length.
"""

import contextlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sized
from email.message import Message
from http import HTTPStatus
from itertools import islice
from urllib.parse import unquote_plus, urlsplit

from podex.database_errors import type_name
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

_MAX_NAME = 256
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_WORD_SPLIT_RE = re.compile(r"[\s.\-]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SAFE_PREFIX_NAME_RE = re.compile(r"[a-z0-9\-]{1,64}")
_HEADER_VALUE_RE = re.compile(r"[A-Za-z0-9 _.:;,=/+*#()\-]{0,128}")
_QUERY_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_QUERY_VALUE_RE = re.compile(r"[A-Za-z0-9_.\-]{0,64}")
_HOST_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]")
_PATH_RE = re.compile(r"/?[A-Za-z0-9._~/\-]{0,512}")
_IDENTIFIER_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]{0,127}(?:\.[A-Za-z_][A-Za-z0-9_]{0,127}){0,8}",
)
_NUMBER_TEXT_RE = re.compile(r"\d{1,12}(?:\.\d{1,6})?")
_SCHEMES = frozenset({"http", "https"})

# URL userinfo and, once configured, the secret values of the loaded settings.
_REDACTOR = SecretRedactor()


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
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    if not isinstance(name, str) or len(name) > _MAX_NAME:
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
    if text is None or is_credential_key(text):
        return None
    lowered = text.strip().lower()
    if lowered in SAFE_HEADERS:
        return lowered
    if lowered.startswith(SAFE_HEADER_PREFIXES) and _SAFE_PREFIX_NAME_RE.fullmatch(
        lowered,
    ):
        return lowered
    return None


def header_value(value: object) -> str:
    """Return an allowlisted header's value if it validates, else withheld.

    Args:
        value: The value, as text, bytes or a number.

    Returns:
        str: The value, or ``[withheld]``.
    """
    if isinstance(value, bytes):
        if len(value) > MAX_HEADER_VALUE:
            return WITHHELD
        value = value.decode("latin-1")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if (
        isinstance(value, str)
        and len(value) <= MAX_HEADER_VALUE
        and _HEADER_VALUE_RE.fullmatch(value)
    ):
        return value
    return WITHHELD


def _size(value: object) -> int:
    """Return a container's size, or 0 if it has none or reading it fails."""
    size = 0
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = len(value)
    return size


def _header_items(headers: object) -> Iterator[tuple[object, object]]:
    """Yield a header container's name and value pairs, lazily."""
    multi_items = getattr(headers, "multi_items", None)
    if callable(multi_items):
        pairs: Iterable[object] = multi_items()
    elif isinstance(headers, Mapping | Message):
        pairs = headers.items()
    elif isinstance(headers, list | tuple):
        pairs = headers
    else:
        pairs = ()
    for pair in islice(pairs, MAX_ITEMS):
        if isinstance(pair, list | tuple) and len(pair) == 2:
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
    others = 0
    if size <= MAX_ITEMS:
        for name, value in _header_items(headers):
            printable = safe_header_name(name) if len(shown) < MAX_HEADERS else None
            if printable is None:
                others += 1
            else:
                shown[printable] = header_value(value)
    else:
        others = size
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
    text = url if isinstance(url, str) else str(url)
    if len(text) > MAX_URL:
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
        host = parts.hostname or ""
        shown_host = host if _HOST_RE.fullmatch(host) else WITHHELD
        authority = f"{parts.scheme}://{shown_host}{f':{port}' if port else ''}"
    path = parts.path if _PATH_RE.fullmatch(parts.path) else f"/{WITHHELD}"
    query = f"?{_query_skeleton(parts.query)}" if parts.query else ""
    return f"{authority}{path}{query}"


def method(value: object) -> str:
    """Return an HTTP method from the fixed set, uppercased, else withheld.

    Args:
        value: The method, as text or bytes.

    Returns:
        str: For example ``POST``.
    """
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if isinstance(value, str) and value.upper() in METHODS:
        return value.upper()
    return WITHHELD


def number(value: object) -> int | float | None:
    """Return a value that is, or parses as, a number; None otherwise.

    Args:
        value: An int, float or numeric text.

    Returns:
        int | float | None: The number.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and _NUMBER_TEXT_RE.fullmatch(value.strip()):
        text = value.strip()
        return float(text) if "." in text else int(text)
    return None


def status(value: object) -> int | None:
    """Return an HTTP status code, or None.

    Args:
        value: The status.

    Returns:
        int | None: A code from 100 to 599.
    """
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
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
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return WITHHELD


def qualified_name(kind: object) -> str:
    """Return a class's module-qualified name, validated as an identifier.

    Args:
        kind: A class.

    Returns:
        str: For example ``httpx2.ConnectError``, or ``[withheld]``.
    """
    module = getattr(kind, "__module__", "")
    name = getattr(kind, "__qualname__", "")
    if not isinstance(name, str):
        return WITHHELD
    if isinstance(module, str) and module not in ("builtins", "__main__", ""):
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
    size = ""
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = f" ({len(value)})"
    return f"[withheld {type_name(value)}{size}]"
