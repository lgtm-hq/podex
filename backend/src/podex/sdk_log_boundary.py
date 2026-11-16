"""Credential boundary for log records written by third-party SDKs.

The HTTP and SDK clients the application uses log on their own loggers
(:data:`SDK_LOGGER_FAMILIES`). At DEBUG, and for some at INFO or ERROR, those
records carry request URLs, request and response headers, request options
with the request body, response bodies, provider messages and
connection-error text. Every record from one of these loggers passes
:func:`sanitise_sdk_record` when it is created. It keeps what an operator
needs to debug (method, scheme, host, path, non-credential query fields,
status, the allowlisted headers, exception classes and frames) and masks the
rest:

1. Known record forms (:mod:`podex.sdk_log_forms`) keep a fixed skeleton,
   or have a form-specific rule applied to their arguments first.
2. Arguments are cleaned by key, then by type, keeping their shape: values
   named like a credential are masked and those named like a body withheld
   (mapping keys and format labels such as ``"Signature: %s"``); header
   containers keep only allowlisted values; URLs lose credentials; bytes are
   withheld; exceptions print their class; other objects are rendered as
   their placeholder would render them and pass the text rules.
3. The message is rendered from the cleaned arguments and the complete text
   passes the text rules (:mod:`podex.sdk_log_rules`). If that changes it,
   the record's message becomes the sanitised text and its arguments are
   cleared, so placeholders inside quotes or after a prefix
   (``'Authorization': '%s'``, ``Bearer %s``) are covered.
4. A logged traceback is printed with frames, classes, chain and exception
   group members, every message withheld; the original exception is
   detached from the record.

Strings are inspected up to a fixed length and containers up to a fixed
number of items. Records from other loggers are untouched.
"""

import contextlib
import re
import traceback
from collections.abc import Iterable, Mapping, Sized
from itertools import islice
from logging import LogRecord

import httpx
import httpx2

from podex.database_errors import (
    MAX_REBUILD_DEPTH,
    MAX_TREE_SIZE,
    placeholder,
    stand_in,
    stand_in_group,
)
from podex.redaction import REDACTED
from podex.sdk_log_forms import fixed_form, prepare_arguments
from podex.sdk_log_rules import (
    MAX_ITEMS,
    MESSAGE_WITHHELD,
    Rendered,
    configure_secret_values,
    describe_exception,
    is_credential_key,
    safe_headers,
    safe_url,
    sanitise_text,
    snake_case,
    withheld,
)

__all__ = ["configure_secret_values", "is_sdk_logger", "sanitise_sdk_record"]

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

BODY_KEYS = frozenset(
    {"body", "content", "data", "extra_json", "files", "json", "json_data", "payload"},
)
"""Field and label names whose values are request or response bodies."""

_MAX_DEPTH = 4
_LABEL_TAIL = 96
_SCALARS = (int, float, bool, type(None), type)
_UNHANDLED = object()
_FORMAT_RE = re.compile(
    r"%(?:\((?P<key>[^)]{0,128})\))?[#0 +\-]{0,4}(?:\*|\d{1,4})?"
    r"(?:\.(?:\*|\d{1,4}))?[hlL]?(?P<conversion>[diouxXeEfFgGcrsa%])",
)
_LABEL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 _.\-]{0,63}?)[ \t]*[:=][ \t\r\n]*$")


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


def _label_kind(label: object) -> str | None:
    """Classify a mapping key or format label: credential, headers or body."""
    if isinstance(label, bytes):
        label = label.decode("latin-1")
    if not isinstance(label, str):
        return None
    if is_credential_key(label):
        return "credential"
    key = snake_case(label)
    last = key.rpartition("_")[2]
    if last == "headers":
        return "headers"
    if key in BODY_KEYS or last in BODY_KEYS:
        return "body"
    return None


def _placeholders(message: str) -> list[tuple[str | None, str, str | None]]:
    """Return each ``%`` placeholder's mapping key, conversion and label.

    The label is the name written just before the placeholder: ``"Response
    headers: %r"`` labels its argument ``Response headers``. Only the last
    characters before each placeholder are examined. ``%%`` is skipped.
    """
    found: list[tuple[str | None, str, str | None]] = []
    position = 0
    for match in _FORMAT_RE.finditer(message):
        conversion = match.group("conversion")
        if conversion != "%":
            tail = message[max(position, match.start() - _LABEL_TAIL) : match.start()]
            label = _LABEL_RE.search(tail)
            found.append(
                (match.group("key"), conversion, label.group(1) if label else None),
            )
        position = match.end()
    return found


def _spend(budget: list[int]) -> bool:
    """Count one inspected item; return False once the budget is exhausted."""
    budget[0] -= 1
    return budget[0] >= 0


def _over_budget(value: object, budget: list[int]) -> bool:
    """Return whether a sized container holds more items than remain."""
    size = 0
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = len(value)
    return size > budget[0]


def _limited(value: object) -> str:
    """Return the placeholder for a container past the inspection bound."""
    text: str = placeholder(value, reason="sanitiser limit reached")
    return text


def _safe_mapping(
    value: Mapping[object, object],
    depth: int,
    budget: list[int],
) -> object:
    """Clean a mapping entry by entry, deciding each value by its key."""
    if _over_budget(value, budget):
        return _limited(value)
    cleaned: dict[object, object] = {}
    for key, item in islice(value.items(), budget[0] + 1):
        if not _spend(budget):
            return _limited(value)
        cleaned[_safe_value(key, None, depth + 1, budget)] = _safe_value(
            item,
            key,
            depth + 1,
            budget,
        )
    return cleaned


def _safe_collection(
    value: Iterable[object],
    depth: int,
    budget: list[int],
) -> object:
    """Clean each item of a sequence or set."""
    if _over_budget(value, budget):
        return _limited(value)
    items: list[object] = []
    for item in islice(value, budget[0] + 1):
        if not _spend(budget):
            return _limited(value)
        items.append(_safe_value(item, None, depth + 1, budget))
    return tuple(items) if isinstance(value, tuple) else items


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
    return Rendered(sanitise_text(text))


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
            f"<{type(value).__name__} method={sanitise_text(str(method))} "
            f"url={safe_url(str(url))} headers={safe_headers(headers)}>"
        )
    status = _attribute(value, "status_code")
    if isinstance(status, int):
        return (
            f"<{type(value).__name__} status={status} headers={safe_headers(headers)}>"
        )
    return None


def _safe_scalar(value: object) -> object:
    """Clean a value whose type alone decides; :data:`_UNHANDLED` otherwise.

    Strings get the text rules; numbers, ``None`` and classes are kept;
    bytes are withheld; exceptions print their class; URLs lose credentials.
    """
    result: object = _UNHANDLED
    if isinstance(value, str):
        result = sanitise_text(value)
    elif isinstance(value, _SCALARS):
        result = value
    elif isinstance(value, bytes | bytearray | memoryview):
        result = withheld(value)
    elif isinstance(value, BaseException):
        result = Rendered(describe_exception(value))
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
    levels and :data:`MAX_ITEMS` items, without reading past the bound;
    request- and response-like objects print their structure; any other
    object is rendered with its placeholder's ``conversion`` and passes the
    text rules.
    """
    if depth >= _MAX_DEPTH:
        return _limited(value)
    if isinstance(value, Mapping):
        return _safe_mapping(value, depth, budget)
    if isinstance(value, list | tuple | set | frozenset):
        return _safe_collection(value, depth, budget)
    structured = _safe_message_object(value)
    if structured is not None:
        return Rendered(structured)
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
        return withheld(value)
    scalar = _safe_scalar(value)
    if scalar is not _UNHANDLED:
        return scalar
    if kind == "headers" or isinstance(value, httpx.Headers | httpx2.Headers):
        return safe_headers(value)
    return _safe_object(value, depth, budget, conversion)


def _withheld_copy(
    error: BaseException,
    memo: dict[int, BaseException],
    depth: int,
) -> BaseException:
    """Return a stand-in tree printing ``error``'s frames and classes only.

    Causes, contexts and exception group members are rebuilt with their own
    classes and frames, up to :data:`MAX_REBUILD_DEPTH` links and
    :data:`MAX_TREE_SIZE` exceptions; past either bound, a placeholder.
    """
    known = memo.get(id(error))
    if known is not None:
        return known
    if depth >= MAX_REBUILD_DEPTH or len(memo) >= MAX_TREE_SIZE:
        limit: BaseException = stand_in(error, _limited(error))
        memo[id(error)] = limit
        return limit
    copy: BaseException = stand_in(error, MESSAGE_WITHHELD)
    memo[id(error)] = copy
    if isinstance(error, BaseExceptionGroup):
        members = [
            _withheld_copy(member, memo, depth + 1)
            for member in error.exceptions[:MAX_TREE_SIZE]
            if isinstance(member, BaseException)
        ]
        if members:
            copy = stand_in_group(error, MESSAGE_WITHHELD, members)
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

    Frames, classes, causes, contexts and exception group members print as
    :mod:`traceback` prints them; each message is replaced by
    ``[message withheld]`` and notes are dropped.

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

    Positional arguments stay a tuple of the same length; a mapping filling
    named placeholders stays a mapping with the same keys.
    """
    budget = [MAX_ITEMS]
    placeholders = _placeholders(message)
    if isinstance(arguments, Mapping) and any(key for key, _, _ in placeholders):
        conversions = {key: conversion for key, conversion, _ in placeholders}
        cleaned: dict[str, object] = {}
        for key, item in islice(arguments.items(), MAX_ITEMS):
            name = str(key)
            cleaned[name] = _safe_value(
                item, key, 1, budget, conversions.get(name, "s")
            )
        return cleaned
    # A single mapping argument is stored as the arguments themselves.
    positional = arguments if isinstance(arguments, tuple) else (arguments,)
    shapes = [(conversion, label) for _, conversion, label in placeholders]
    shapes = (shapes + [("s", None)] * len(positional))[: len(positional)]
    return tuple(
        _safe_value(item, label, 1, budget, conversion)
        for item, (conversion, label) in zip(positional, shapes, strict=True)
    )


def _message_text(message: object) -> str:
    """Return a record's message as text: an exception by its class only."""
    if isinstance(message, BaseException):
        described: str = describe_exception(message)
        return described
    return message if isinstance(message, str) else str(message)


def sanitise_sdk_record(record: LogRecord) -> None:
    """Mask credentials, bodies, provider text and error text in an SDK record.

    A known record form keeps its skeleton. Otherwise the arguments are
    cleaned, the message is rendered from them and the rendered text passes
    the text rules; if they change it, the message becomes the sanitised
    text and the arguments are cleared, otherwise the cleaned arguments are
    kept. A logged exception is replaced by its withheld traceback text and
    detached. Raises on internal failure; the caller then replaces the whole
    record with a placeholder.

    Args:
        record: A record from a logger in :data:`SDK_LOGGER_FAMILIES`.
    """
    text = _message_text(record.msg)
    arguments = record.args
    fixed = fixed_form(record.name, text, arguments)
    if fixed is not None:
        record.msg, record.args = fixed
    elif arguments:
        cleaned = _clean_arguments(
            text,
            prepare_arguments(record.name, text, arguments),
        )
        rendered = text % cleaned
        safe = sanitise_text(rendered)
        if safe == rendered:
            record.msg, record.args = text, cleaned
        else:
            record.msg, record.args = safe, ()
    else:
        record.msg, record.args = sanitise_text(text), ()
    exc_info = record.exc_info
    if isinstance(exc_info, tuple) and isinstance(exc_info[1], BaseException):
        record.exc_text = format_withheld_exception(exc_info[1])
    elif isinstance(record.exc_text, str):
        record.exc_text = sanitise_text(record.exc_text)
    record.exc_info = None
