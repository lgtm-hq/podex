"""Credential boundary for log records written by third-party SDKs.

The HTTP and SDK clients the application uses log on their own loggers
(:data:`SDK_LOGGER_FAMILIES`). At DEBUG, and for some at INFO, those records
carry request URLs, request and response headers, request options with the
request body, response bodies and connection-error text. Every record from
one of these loggers passes :func:`sanitise_sdk_record` when it is created,
which keeps what an operator needs to debug (method, scheme, host, path,
non-credential query fields, status, the allowlisted headers, exception
classes and frames) and masks the rest:

- Arguments are cleaned by type and by key, keeping their shape:
  URLs lose userinfo, fragment and credential query values; header
  containers keep only :data:`SAFE_HEADERS` values; mapping entries and
  format labels (``"Signature: %s"``) named like a credential are masked,
  and those named like a body (``"Response body: %r"``, ``json_data``) are
  withheld; bytes are withheld; exceptions print their class only.
- Other objects are rendered as their placeholder would render them, and
  that text, string arguments and the message's literal text pass the text
  rules, which also cover SDKs that format their messages themselves:
  credential ``name: value`` and ``name=value`` fields, wire header pairs
  (``(b'Name', b'value')``) outside the allowlist, exception reprs after
  ``exception=``, URL userinfo and the configured secret values.
- A logged traceback is printed with frames and classes and every message
  withheld; the original exception is detached from the record.

Records from other loggers are untouched.
"""

import contextlib
import re
import traceback
from collections.abc import Collection, Iterable, Mapping, Sized
from email.message import Message
from logging import LogRecord
from urllib.parse import unquote_plus, urlsplit

import httpx
import httpx2

from podex.database_errors import (
    MAX_REBUILD_DEPTH,
    MAX_TREE_SIZE,
    placeholder,
    stand_in,
    type_name,
)
from podex.redaction import REDACTED, SecretRedactor

SDK_LOGGER_FAMILIES = (
    "anthropic",
    "httpx",
    "httpx2",
    "httpcore",
    "httpcore2",
    "urllib3",
    "requests",
    "spotipy",
    "boto3",
    "botocore",
    "s3transfer",
    "uvicorn.access",
)
"""Loggers (with their children) whose records pass the SDK boundary."""

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
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "awsaccesskeyid",
        "client_secret",
        "code",
        "code_verifier",
        "cookie",
        "credential",
        "credentials",
        "id_token",
        "jwt",
        "key",
        "passwd",
        "password",
        "proxy_authorization",
        "pwd",
        "refresh_token",
        "secret",
        "session",
        "set_cookie",
        "sig",
        "signature",
        "sslpassword",
        "token",
    },
)
"""Field, header and query names whose values are credentials."""

CREDENTIAL_SUFFIXES = (
    "_auth",
    "_credential",
    "_key",
    "_password",
    "_secret",
    "_signature",
    "_token",
)
"""Name endings that mark a credential (``x_api_key``, ``security_token``)."""

BODY_KEYS = frozenset(
    {"body", "content", "data", "extra_json", "files", "json", "json_data", "payload"},
)
"""Field and label names whose values are request or response bodies."""

MESSAGE_WITHHELD = "[message withheld]"

_MAX_DEPTH = 4
_MAX_ITEMS = 1_000
_SCALARS = (int, float, bool, type(None))
_UNHANDLED = object()
_CAMEL_SUFFIXES = ("Token", "Key", "Secret", "Password", "Signature", "Credential")
_FORMAT_RE = re.compile(
    r"%(?:\((?P<key>[^)]*)\))?[#0 +\-]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?"
    r"(?P<conversion>[diouxXeEfFgGcrsa%])",
)
_LABEL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 _.\-]*?)\s*[:=]\s*$")
_WORD_SPLIT_RE = re.compile(r"[\s.\-]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_HEADER_PAIR_RE = re.compile(
    r"""\(b(?P<nq>['"])(?P<name>[A-Za-z0-9!#$%&*+.^_`|~\-]+)(?P=nq),\s*"""
    r"""b(?P<q>['"])(?P<value>(?:\\.|(?!(?P=q)).)*)(?P=q)\)""",
)


# An exception rendered with repr() after an ``exception=`` field, as HTTP
# transports trace a failed step: the class is kept, the arguments withheld.
_EXCEPTION_REPR_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<head>(?:exception|exc|error)\s*[:=]\s*"
    r"[A-Za-z_][A-Za-z0-9_.]*)\([^\r\n]*\)",
)


def _key_pattern() -> str:
    """Return the pattern matching a credential name in free text."""
    names = sorted(CREDENTIAL_KEYS, key=len, reverse=True)
    exact = "|".join(re.escape(name).replace("_", "[-_.]") for name in names)
    suffix = "|".join(re.escape(end[1:]) for end in CREDENTIAL_SUFFIXES)
    camel = "|".join(_CAMEL_SUFFIXES)
    return (
        rf"(?:[A-Za-z0-9_.\-]*[-_.](?:{suffix})|(?-i:[A-Za-z0-9]*[a-z0-9](?:{camel}))"
        rf"|{exact})"
    )


_CREDENTIAL_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<name>" + _key_pattern() + r""")(?P<close>["']?)"""
    r"(?P<sep>\s*(?:(?P<colon>:)|=)\s*)"
    r"(?:(?P<done>\[redacted\])"
    r"""|(?P<prefix>b?)(?P<q>["'])(?:\\.|(?!(?P=q)).)*(?P=q)"""
    r"""|(?(colon)[^\r\n,;}'"]*|[^\s&#,;'"}\])]*))""",
    re.IGNORECASE,
)

# URL userinfo and, once configured, the secret values of the loaded settings.
_REDACTOR = SecretRedactor()


def configure_secret_values(secrets: Iterable[str]) -> None:
    """Set the configured secret values replaced in SDK records.

    Args:
        secrets: Secret values of the loaded settings; empty values are ignored.
    """
    _REDACTOR.configure(secrets)


def is_sdk_logger(name: object) -> bool:
    """Return whether records from logger ``name`` pass the SDK boundary.

    Args:
        name: A logger name.

    Returns:
        bool: True for a logger in :data:`SDK_LOGGER_FAMILIES` or a child of one.
    """
    if not isinstance(name, str):
        return False
    families = _FAMILIES_BY_ROOT.get(name.partition(".")[0])
    if families is None:
        return False
    return any(name == family or name.startswith(f"{family}.") for family in families)


def _families_by_root() -> dict[str, tuple[str, ...]]:
    """Index :data:`SDK_LOGGER_FAMILIES` by their first name component."""
    index: dict[str, tuple[str, ...]] = {}
    for family in SDK_LOGGER_FAMILIES:
        root = family.partition(".")[0]
        index[root] = (*index.get(root, ()), family)
    return index


_FAMILIES_BY_ROOT = _families_by_root()


def _normalised(name: str) -> str:
    """Return a field name in snake case: ``X-Api-Key`` becomes ``x_api_key``."""
    return "_".join(
        part for part in _WORD_SPLIT_RE.split(_CAMEL_RE.sub("_", name).lower()) if part
    )


def is_credential_key(name: object) -> bool:
    """Return whether a field, header or query name holds a credential.

    Args:
        name: The name, in any case, with ``-``, ``.``, spaces or camel case.

    Returns:
        bool: True for :data:`CREDENTIAL_KEYS` and :data:`CREDENTIAL_SUFFIXES`.
    """
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    if not isinstance(name, str):
        return False
    key = _normalised(name)
    return key in CREDENTIAL_KEYS or key.endswith(CREDENTIAL_SUFFIXES)


def is_safe_header(name: object) -> bool:
    """Return whether a header's value may be logged.

    Args:
        name: The header name, as text or bytes.

    Returns:
        bool: True for :data:`SAFE_HEADERS` and :data:`SAFE_HEADER_PREFIXES`.
    """
    if isinstance(name, bytes):
        name = name.decode("latin-1")
    if not isinstance(name, str):
        return False
    lowered = name.strip().lower()
    return lowered in SAFE_HEADERS or lowered.startswith(SAFE_HEADER_PREFIXES)


def _mask_field(match: re.Match[str]) -> str:
    """Replace one credential field's value, keeping its name and quoting."""
    if match.group("done") is not None or match.group(0).endswith(
        match.group("sep"),
    ):
        return match.group(0)
    head = f"{match.group('name')}{match.group('close')}{match.group('sep')}"
    quote_char = match.group("q")
    if quote_char is not None:
        return f"{head}{match.group('prefix')}{quote_char}{REDACTED}{quote_char}"
    return f"{head}{REDACTED}"


def mask_credential_fields(text: str) -> str:
    """Mask the values of credential-named fields in free text.

    Covers ``name: value`` (header lines, rendered mappings, quoted or bytes
    values) and ``name=value`` (query strings, form bodies, signed header
    parameters); the name and quoting are kept.

    Args:
        text: A rendered log message.

    Returns:
        str: The message with credential values replaced by ``[redacted]``.
    """
    return _CREDENTIAL_FIELD_RE.sub(_mask_field, text)


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
    """Apply every text rule of the SDK boundary to a rendered message.

    Args:
        text: A rendered log message or argument.

    Returns:
        str: The text without wire header values outside the allowlist,
            credential field values, URL userinfo or configured secrets.
    """
    text = _HEADER_PAIR_RE.sub(_mask_header_pair, text)
    text = _EXCEPTION_REPR_RE.sub(rf"\g<head>({MESSAGE_WITHHELD})", text)
    redacted: str = _REDACTOR.redact(mask_credential_fields(text))
    return redacted


def _masked_query(query: str) -> str:
    """Mask the values of credential query fields; keep the others."""
    fields: list[str] = []
    for field in query.split("&"):
        name, equals, _value = field.partition("=")
        if equals and is_credential_key(unquote_plus(name)):
            fields.append(f"{name}={REDACTED}")
        else:
            fields.append(field)
    return "&".join(fields)


def safe_url(url: str) -> str:
    """Render a URL without userinfo, fragment or credential query values.

    Args:
        url: An absolute or relative URL.

    Returns:
        str: For example ``https://[redacted]@host/path?token=[redacted]&page=2``.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return sanitise_text(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = f"{REDACTED}@{netloc.rpartition('@')[2]}"
    scheme = f"{parts.scheme}:" if parts.scheme else ""
    authority = f"//{netloc}" if netloc or url.startswith(f"{scheme}//") else ""
    query = f"?{_masked_query(parts.query)}" if parts.query else ""
    fragment = f"#{REDACTED}" if parts.fragment else ""
    return sanitise_text(f"{scheme}{authority}{parts.path}{query}{fragment}")


def _withheld(value: object) -> str:
    """Describe a withheld body by type and size."""
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


class _Rendered(str):
    """Text standing in for an object; ``%r`` prints it without quotes."""

    __slots__ = ()

    def __repr__(self) -> str:
        """Return the text itself, as the object's own repr would print."""
        return str(self)


def describe_exception(error: BaseException) -> str:
    """Describe an exception by class, without its message.

    Args:
        error: The exception.

    Returns:
        str: For example ``"httpx2.ConnectError: [message withheld]"``.
    """
    return f"{_qualified_name(error)}: {MESSAGE_WITHHELD}"


def _header_items(headers: object) -> list[tuple[object, object]]:
    """Return a header container's name and value pairs, repeats included."""
    multi_items = getattr(headers, "multi_items", None)
    if callable(multi_items):
        return list(multi_items())
    if isinstance(headers, Mapping | Message):
        return list(headers.items())
    if isinstance(headers, list | tuple):
        return [
            (pair[0], pair[1])
            for pair in headers
            if isinstance(pair, list | tuple) and len(pair) == 2
        ]
    return []


def _safe_header_value(value: object) -> object:
    """Return an allowlisted header's value after the text rules."""
    if isinstance(value, str):
        return sanitise_text(value)
    if isinstance(value, bytes):
        text = value.decode("latin-1")
        cleaned = sanitise_text(text)
        return value if cleaned == text else cleaned
    return value if isinstance(value, _SCALARS) else REDACTED


def safe_headers(headers: object) -> dict[object, object]:
    """Return a header container with only allowlisted values kept.

    Args:
        headers: A header mapping, header object or sequence of pairs.

    Returns:
        dict: Every header name; values outside :data:`SAFE_HEADERS` masked.
    """
    kept: dict[object, object] = {}
    for name, value in _header_items(headers)[:_MAX_ITEMS]:
        shown = _safe_header_value(value) if is_safe_header(name) else REDACTED
        kept[name] = f"{kept[name]}, {shown}" if name in kept else shown
    return kept


def _label_kind(label: object) -> str | None:
    """Classify a mapping key or format label: credential, headers or body."""
    if isinstance(label, bytes):
        label = label.decode("latin-1")
    if not isinstance(label, str):
        return None
    key = _normalised(label)
    last = key.rpartition("_")[2]
    if is_credential_key(key):
        return "credential"
    if last == "headers":
        return "headers"
    if key in BODY_KEYS or last in BODY_KEYS:
        return "body"
    return None


def _placeholders(message: str) -> list[tuple[str | None, str, str | None]]:
    """Return each ``%`` placeholder's mapping key, conversion and label.

    The label is the name written before the placeholder: ``"Response
    headers: %r"`` labels its argument ``Response headers``. ``%%`` is skipped.
    """
    found: list[tuple[str | None, str, str | None]] = []
    position = 0
    for match in _FORMAT_RE.finditer(message):
        conversion = match.group("conversion")
        if conversion != "%":
            label = _LABEL_RE.search(message[position : match.start()])
            found.append(
                (match.group("key"), conversion, label.group(1) if label else None),
            )
        position = match.end()
    return found


def _sanitise_literals(message: str) -> str:
    """Apply the text rules to a format string's literal text only.

    Placeholders are kept as they are, so a masked value can never change
    how the arguments are formatted.
    """
    parts: list[str] = []
    position = 0
    for match in _FORMAT_RE.finditer(message):
        parts.append(sanitise_text(message[position : match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(sanitise_text(message[position:]))
    return "".join(parts)


def _spend(budget: list[int]) -> bool:
    """Count one inspected item; return False once the budget is exhausted."""
    budget[0] -= 1
    return budget[0] >= 0


def _rendered(value: object, conversion: str) -> object:
    """Render an object as its placeholder would, then apply the text rules.

    Numeric conversions print a number, never text, so the value is kept.
    """
    if conversion not in ("r", "s", "a"):
        return value
    if conversion == "r":
        text = repr(value)
    elif conversion == "a":
        text = ascii(value)
    else:
        text = str(value)
    return _Rendered(sanitise_text(text))


def _safe_mapping(
    value: Mapping[object, object], depth: int, budget: list[int]
) -> object:
    """Clean a mapping entry by entry, deciding each value by its key."""
    cleaned: dict[object, object] = {}
    for key, item in value.items():
        if not _spend(budget):
            return placeholder(value, reason="sanitiser limit reached")
        cleaned[_safe_value(key, None, depth + 1, budget)] = _safe_value(
            item, key, depth + 1, budget
        )
    return cleaned


def _safe_collection(
    value: Collection[object], depth: int, budget: list[int]
) -> object:
    """Clean each item of a sequence or set."""
    items: list[object] = []
    for item in value:
        if not _spend(budget):
            return placeholder(value, reason="sanitiser limit reached")
        items.append(_safe_value(item, None, depth + 1, budget))
    return tuple(items) if isinstance(value, tuple) else items


def _attribute(value: object, name: str) -> object:
    """Return an attribute, or None if it is missing or reading it fails."""
    found: object = None
    with contextlib.suppress(Exception):
        found = getattr(value, name, None)
    return found


def _safe_message_object(value: object) -> str | None:
    """Render a request- or response-like object by its structure, if it is one.

    An object with ``method``, ``url`` and ``headers`` (an SDK's prepared
    request) prints its method, safe URL and allowlisted headers; one with
    ``status_code`` and ``headers`` prints its status and headers. Bodies are
    never printed.
    """
    headers = _attribute(value, "headers")
    if headers is None:
        return None
    method = _attribute(value, "method")
    url = _attribute(value, "url")
    if method is not None and url is not None:
        return (
            f"<{type_name(value)} method={sanitise_text(str(method))} "
            f"url={safe_url(str(url))} headers={safe_headers(headers)}>"
        )
    status = _attribute(value, "status_code")
    if isinstance(status, int):
        return f"<{type_name(value)} status={status} headers={safe_headers(headers)}>"
    return None


def _safe_scalar(value: object) -> object:
    """Clean a value whose type alone decides; :data:`_UNHANDLED` otherwise.

    Strings get the text rules; numbers, ``None`` and classes are kept;
    bytes are withheld; exceptions print their class; URLs lose credentials.
    """
    result: object = _UNHANDLED
    if isinstance(value, str):
        result = sanitise_text(value)
    elif isinstance(value, (*_SCALARS, type)):
        result = value
    elif isinstance(value, bytes | bytearray | memoryview):
        result = _withheld(value)
    elif isinstance(value, BaseException):
        result = _Rendered(describe_exception(value))
    elif isinstance(value, httpx.URL | httpx2.URL):
        result = safe_url(str(value))
    return result


def _safe_object(
    value: object,
    depth: int,
    budget: list[int],
    conversion: str,
) -> object:
    """Clean a container or an arbitrary object.

    Containers are rebuilt from cleaned items, up to :data:`_MAX_DEPTH`
    levels; request- and response-like objects print their structure; any
    other object is rendered with its placeholder's ``conversion`` and
    passes the text rules.
    """
    if depth >= _MAX_DEPTH:
        return placeholder(value, reason="sanitiser limit reached")
    if isinstance(value, Mapping):
        return _safe_mapping(value, depth, budget)
    if isinstance(value, list | tuple | set | frozenset):
        return _safe_collection(value, depth, budget)
    structured = _safe_message_object(value)
    if structured is not None:
        return _Rendered(structured)
    return _rendered(value, conversion)


def _safe_value(
    value: object,
    label: object,
    depth: int,
    budget: list[int],
    conversion: str = "r",
) -> object:
    """Return ``value`` as it may be logged, deciding by its key, then its type.

    A value named like a credential is masked and one named like a body is
    withheld; a value under a ``headers`` name, or a header container, keeps
    only allowlisted header values. Objects are rendered with the
    placeholder's ``conversion`` (``repr`` inside containers).
    """
    kind = _label_kind(label)
    if value is not None and kind == "credential":
        return REDACTED
    if value is not None and kind == "body":
        return _withheld(value)
    scalar = _safe_scalar(value)
    if scalar is not _UNHANDLED:
        return scalar
    if kind == "headers" or isinstance(value, httpx.Headers | httpx2.Headers | Message):
        return safe_headers(value)
    return _safe_object(value, depth, budget, conversion)


def _withheld_copy(
    error: BaseException,
    memo: dict[int, BaseException],
    depth: int,
) -> BaseException:
    """Return a stand-in tree printing ``error``'s frames and classes only."""
    known = memo.get(id(error))
    if known is not None:
        return known
    if depth >= MAX_REBUILD_DEPTH or len(memo) >= MAX_TREE_SIZE:
        limit: BaseException = stand_in(
            error,
            placeholder(error, reason="sanitiser limit reached"),
        )
        memo[id(error)] = limit
        return limit
    message = MESSAGE_WITHHELD
    if isinstance(error, BaseExceptionGroup):
        message = f"{MESSAGE_WITHHELD} ({len(error.exceptions)} sub-exceptions)"
    copy: BaseException = stand_in(error, message)
    memo[id(error)] = copy
    copy.__traceback__ = error.__traceback__
    if isinstance(error.__cause__, BaseException):
        copy.__cause__ = _withheld_copy(error.__cause__, memo, depth + 1)
    if isinstance(error.__context__, BaseException) and not error.__suppress_context__:
        copy.__context__ = _withheld_copy(error.__context__, memo, depth + 1)
    copy.__suppress_context__ = error.__suppress_context__
    return copy


def format_withheld_exception(error: BaseException) -> str:
    """Format a traceback with every exception message withheld.

    Frames, classes, causes and contexts print as :mod:`traceback` prints
    them; each message (and note) is replaced by ``[message withheld]``.

    Args:
        error: The exception logged by an SDK.

    Returns:
        str: The traceback text, without a trailing newline.
    """
    printed = _withheld_copy(error, {}, 0)
    return "".join(traceback.format_exception(printed)).rstrip("\n")


def _clean_arguments(
    message: str,
    arguments: object,
) -> tuple[object, ...] | dict[str, object]:
    """Return a record's arguments cleaned, in the shape formatting expects.

    Positional arguments stay a tuple of the same length (formatters such
    as the ASGI server's access formatter unpack it); a mapping filling
    named placeholders stays a mapping with the same keys.
    """
    budget = [_MAX_ITEMS]
    placeholders = _placeholders(message)
    if isinstance(arguments, Mapping) and any(key for key, _, _ in placeholders):
        conversions = {key: conversion for key, conversion, _ in placeholders}
        return {
            str(key): _safe_value(item, key, 1, budget, conversions.get(str(key), "s"))
            for key, item in arguments.items()
        }
    # A single mapping argument is stored as the arguments themselves.
    positional = arguments if isinstance(arguments, tuple) else (arguments,)
    shapes = [(conversion, label) for _, conversion, label in placeholders]
    shapes = (shapes + [("s", None)] * len(positional))[: len(positional)]
    return tuple(
        _safe_value(item, label, 1, budget, conversion)
        for item, (conversion, label) in zip(positional, shapes, strict=True)
    )


def sanitise_sdk_record(record: LogRecord) -> None:
    """Mask credentials, bodies and error text in one SDK log record.

    The arguments are cleaned in place of the originals, keeping their
    shape, and the message's literal text passes the text rules; a message
    without arguments passes them whole. A logged exception is replaced by
    its withheld traceback text and detached. Raises on internal failure;
    the caller replaces the record with a placeholder then.

    Args:
        record: A record from a logger in :data:`SDK_LOGGER_FAMILIES`.
    """
    message = record.msg
    if isinstance(message, BaseException):
        text = describe_exception(message)
    else:
        text = message if isinstance(message, str) else str(message)
    if record.args:
        record.args = _clean_arguments(text, record.args)
        record.msg = _sanitise_literals(text)
    else:
        record.msg = sanitise_text(text)
    exc_info = record.exc_info
    if isinstance(exc_info, tuple) and isinstance(exc_info[1], BaseException):
        record.exc_text = format_withheld_exception(exc_info[1])
    elif isinstance(record.exc_text, str):
        record.exc_text = sanitise_text(record.exc_text)
    record.exc_info = None
