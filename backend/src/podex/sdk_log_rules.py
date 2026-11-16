"""Key, header and text rules of the third-party SDK log boundary.

:mod:`podex.sdk_log_boundary` decides what a record keeps; this module holds
the rules it applies:

- :func:`is_credential_key` classifies a field, header or query name in any
  case and spelling (``ToKeN``, ``AWSAccessKeyId``, ``x-amz-signature``,
  ``api%5Fkey``).
- :func:`is_safe_header` is the header allowlist; :func:`safe_headers`
  applies it to a header container.
- :func:`safe_url` and :func:`sanitise_text` remove credentials from a URL
  or from free text: credential ``name: value`` and ``name=value`` fields
  (query names percent-decoded), wire header pairs outside the allowlist,
  exception reprs after ``exception=``, URL userinfo and the configured
  secret values.

Every scan is linear and capped: a string is inspected up to
:data:`MAX_TEXT` characters and the remainder replaced by a marker, and
containers are read up to :data:`MAX_ITEMS` entries.
"""

import contextlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sized
from email.message import Message
from http import HTTPStatus
from itertools import islice
from urllib.parse import unquote_plus, urlsplit

from podex.database_errors import placeholder, type_name
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
"""Header names whose values are logged; every other header value is masked."""

SAFE_HEADER_PREFIXES = ("anthropic-ratelimit-", "x-ratelimit-")
"""Rate-limit header families, logged for diagnosing throttling."""

CREDENTIAL_KEYS = frozenset(
    {
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "awsaccesskeyid",
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
        "session",
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

MESSAGE_WITHHELD = "[message withheld]"

MAX_TEXT = 8_192
"""Characters of one string or rendered message inspected; the rest is cut."""

MAX_ITEMS = 1_000
"""Entries of one record's containers inspected; beyond, a placeholder."""

_MAX_NAME = 256
_CUT_WINDOW = 256
_CUT_DELIMITERS = " \t\r\n,;&'\"{}()[]<>"
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_WORD_SPLIT_RE = re.compile(r"[\s.\-]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_FIELD_NAME_RE = re.compile(
    r"""(?<![A-Za-z0-9_.%\-])(?P<name>[A-Za-z0-9_.%\-]{1,128})(?P<close>["']?)"""
    r"(?P<sep>[ \t]*(?:(?P<colon>:)|=)[ \t]*(?:\r?\n)?[ \t]*)",
)
_BARE_AFTER_COLON_RE = re.compile(r"""[^\r\n,;}'"]*""")
_BARE_AFTER_EQUALS_RE = re.compile(r"""[^\s&#,;'"}\])]*""")
_QUOTED_REST_RE = {
    "'": re.compile(r"(?:[^'\\]|\\.)*'", re.DOTALL),
    '"': re.compile(r'(?:[^"\\]|\\.)*"', re.DOTALL),
}
_HEADER_PAIR_RE = re.compile(
    r"""\(b(?P<nq>['"])(?P<name>[A-Za-z0-9!#$%&*+.^_`|~\-]{1,128})(?P=nq),[ ]?"""
    r"""b(?P<q>['"])(?:[^'"\\]|\\.|(?!(?P=q))['"])*(?P=q)\)""",
)
# An exception rendered with repr() after an ``exception=`` field, as HTTP
# transports trace a failed step: the class is kept, the arguments withheld.
_EXCEPTION_REPR_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<head>(?:exception|exc|error)[ ]?[:=][ ]?"
    r"[A-Za-z_][A-Za-z0-9_.]{0,128})\([^\r\n]*\)",
)

# URL userinfo and, once configured, the secret values of the loaded settings.
_REDACTOR = SecretRedactor()


class Rendered(str):
    """Text standing in for an object; ``%r`` prints it without quotes."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Return the text itself, as the object's own repr would print."""
        return str(self)


def configure_secret_values(secrets: Iterable[str]) -> None:
    """Set the configured secret values replaced in SDK records.

    Args:
        secrets: Secret values of the loaded settings; empty values are ignored.
    """
    _REDACTOR.configure(secrets)


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


def is_safe_header(name: object) -> bool:
    """Return whether a header's value may be logged.

    Args:
        name: The header name, as text or bytes.

    Returns:
        bool: True for :data:`SAFE_HEADERS` and :data:`SAFE_HEADER_PREFIXES`.
    """
    text = _text_name(name)
    if text is None:
        return False
    lowered = text.strip().lower()
    return lowered in SAFE_HEADERS or lowered.startswith(SAFE_HEADER_PREFIXES)


def reason_phrase(status: object) -> str:
    """Return the standard reason phrase for a status, or ``""``.

    Args:
        status: An HTTP status code.

    Returns:
        str: For example ``"Internal Server Error"`` for 500.
    """
    phrase = ""
    if isinstance(status, int) and not isinstance(status, bool):
        with contextlib.suppress(ValueError):
            phrase = HTTPStatus(status).phrase
    return phrase


def cap_text(text: str) -> str:
    """Cut text to :data:`MAX_TEXT` characters, at a delimiter if one is near.

    Args:
        text: Any text.

    Returns:
        str: The text, or its head and ``[N characters withheld]``.
    """
    if len(text) <= MAX_TEXT:
        return text
    window = text[MAX_TEXT - _CUT_WINDOW : MAX_TEXT]
    boundary = max(window.rfind(delimiter) for delimiter in _CUT_DELIMITERS)
    cut = MAX_TEXT - _CUT_WINDOW + boundary if boundary >= 0 else MAX_TEXT
    return f"{text[:cut]}[{len(text) - cut} characters withheld]"


def _value_end(text: str, start: int, colon: bool) -> tuple[int, str]:
    """Return where a field value starting at ``start`` ends, and its mask.

    A quoted (or ``b``-prefixed quoted) value runs to its closing quote, or
    to the end if unclosed; a bare value after ``:`` runs to the end of the
    line or a separator, after ``=`` to the next query or field separator.
    """
    if text.startswith(REDACTED, start):
        return start, ""
    prefix = ""
    position = start
    if text.startswith(("b'", 'b"'), position):
        prefix = "b"
        position += 1
    if position < len(text) and text[position] in _QUOTED_REST_RE:
        quote_char = text[position]
        closed = _QUOTED_REST_RE[quote_char].match(text, position + 1)
        end = closed.end() if closed else len(text)
        return end, f"{prefix}{quote_char}{REDACTED}{quote_char}"
    bare = (_BARE_AFTER_COLON_RE if colon else _BARE_AFTER_EQUALS_RE).match(
        text,
        start,
    )
    return (bare.end() if bare else start), REDACTED


def mask_credential_fields(text: str) -> str:
    """Mask the values of credential-named fields in free text.

    Covers ``name: value`` (header lines, rendered mappings, quoted or bytes
    values) and ``name=value`` (query strings with percent-encoded names,
    form bodies, signed header parameters); the name and quoting are kept.
    One pass, left to right; a non-credential name never hides the fields
    after it.

    Args:
        text: A rendered log message.

    Returns:
        str: The message with credential values replaced by ``[redacted]``.
    """
    parts: list[str] = []
    position = 0
    for field in _FIELD_NAME_RE.finditer(text):
        if field.start() < position or not is_credential_key(field.group("name")):
            continue
        start = field.end()
        end, mask = _value_end(text, start, field.group("colon") is not None)
        if end == start:
            continue
        parts.append(text[position:start])
        parts.append(mask)
        position = end
    if not parts:
        return text
    parts.append(text[position:])
    return "".join(parts)


def _mask_header_pair(match: re.Match[str]) -> str:
    """Replace the value of one wire header pair unless it is allowlisted."""
    if is_safe_header(match.group("name")):
        return match.group(0)
    quote_char = match.group("q")
    return (
        f"(b{match.group('nq')}{match.group('name')}{match.group('nq')}, "
        f"b{quote_char}{REDACTED}{quote_char})"
    )


def sanitise_text(text: str) -> str:
    """Apply every text rule of the SDK boundary to a string.

    Args:
        text: A rendered log message or argument.

    Returns:
        str: The capped text without wire header values outside the
            allowlist, exception messages, credential field values, URL
            userinfo or configured secrets.
    """
    text = cap_text(text)
    text = _HEADER_PAIR_RE.sub(_mask_header_pair, text)
    text = _EXCEPTION_REPR_RE.sub(rf"\g<head>({MESSAGE_WITHHELD})", text)
    redacted: str = _REDACTOR.redact(mask_credential_fields(text))
    return redacted


def masked_query(query: str) -> str:
    """Mask the values of credential query fields; keep the others."""
    fields: list[str] = []
    for field in query.split("&"):
        name, equals, _value = field.partition("=")
        if equals and is_credential_key(name):
            fields.append(f"{name}={REDACTED}")
        else:
            fields.append(field)
    return "&".join(fields)


def safe_url(url: str) -> str:
    """Render a URL or request target without credentials.

    Userinfo and fragment become ``[redacted]``; credential query values
    (names percent-decoded) become ``[redacted]``; the scheme, host, path
    and other query fields are kept.

    Args:
        url: An absolute or relative URL, or a request target.

    Returns:
        str: For example ``https://[redacted]@host/path?token=[redacted]&page=2``.
    """
    url = cap_text(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return sanitise_text(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    scheme = f"{parts.scheme}:" if parts.scheme else ""
    authority = f"//{netloc}" if netloc or url.startswith(f"{scheme}//") else ""
    query = f"?{masked_query(parts.query)}" if parts.query else ""
    fragment = f"#{REDACTED}" if parts.fragment else ""
    return sanitise_text(f"{scheme}{authority}{parts.path}{query}{fragment}")


def withheld(value: object) -> str:
    """Describe a withheld body by type and size.

    Args:
        value: The body.

    Returns:
        str: For example ``"[withheld bytes (71)]"``.
    """
    size = ""
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = f" ({len(value)})"
    return f"[withheld {type_name(value)}{size}]"


def _qualified_name(error: BaseException) -> str:
    """Return an exception's module-qualified class name."""
    kind = type(error)
    module = getattr(kind, "__module__", "")
    qualname = getattr(kind, "__qualname__", type_name(error))
    if not isinstance(module, str) or module in ("builtins", "__main__"):
        return str(qualname)
    return f"{module}.{qualname}"


def describe_exception(error: BaseException) -> str:
    """Describe an exception by class, without its message.

    Args:
        error: The exception.

    Returns:
        str: For example ``"httpx2.ConnectError: [message withheld]"``.
    """
    return f"{_qualified_name(error)}: {MESSAGE_WITHHELD}"


def _size(value: object) -> int:
    """Return a container's size, or 0 if it has none or reading it fails."""
    size = 0
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = len(value)
    return size


def _header_items(headers: object) -> Iterator[tuple[object, object]]:
    """Yield a header container's name and value pairs, repeats included."""
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


def _safe_header_value(value: object) -> object:
    """Return an allowlisted header's value after the text rules."""
    if isinstance(value, str):
        return sanitise_text(value)
    if isinstance(value, bytes):
        text = value[:MAX_TEXT].decode("latin-1")
        cleaned = sanitise_text(text)
        return value if cleaned == text and len(value) <= MAX_TEXT else cleaned
    return value if isinstance(value, int | float) else REDACTED


def safe_headers(headers: object) -> object:
    """Return a header container with only allowlisted values kept.

    A container larger than :data:`MAX_ITEMS` is not enumerated: it
    becomes a placeholder.

    Args:
        headers: A header mapping, header object or sequence of pairs.

    Returns:
        object: A dict of every header name, values outside
            :data:`SAFE_HEADERS` masked; or the limit placeholder.
    """
    if _size(headers) > MAX_ITEMS:
        return placeholder(headers, reason="sanitiser limit reached")
    kept: dict[object, object] = {}
    for name, value in _header_items(headers):
        shown = _safe_header_value(value) if is_safe_header(name) else REDACTED
        kept[name] = f"{kept[name]}, {shown}" if name in kept else shown
    return kept


def header_value(name: str, value: object) -> object:
    """Return one header's value as it may be logged.

    Args:
        name: The header name.
        value: Its value, as the SDK logs it.

    Returns:
        object: The value after the text rules if the header is allowlisted
            (or absent), otherwise ``[redacted]``.
    """
    if value is None:
        return None
    return _safe_header_value(value) if is_safe_header(name) else REDACTED
