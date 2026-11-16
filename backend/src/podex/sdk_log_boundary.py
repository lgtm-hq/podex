"""Credential boundary for log records written by third-party SDKs.

The HTTP and SDK clients the application uses log on their own loggers
(:data:`SDK_LOGGER_FAMILIES`). Every record from one of these loggers passes
:func:`sanitise_sdk_record` when it is created:

1. A known record form (:mod:`podex.sdk_log_forms`) is written as its
   skeleton, at any level.
2. Any other record below INFO is withheld: DEBUG output of third-party
   libraries is allowlisted by form. The record keeps its logger name and
   level and its message becomes a fixed marker; it is neither rendered nor
   traversed, so withholding it is cheap.
3. Any other record at INFO or above is cleaned within a record-wide budget:
   arguments by key, then by type, keeping their shape (credential-named
   values masked, body-named values and bytes withheld, header containers
   allowlisted, URLs without credentials, exceptions by class); then the
   message is rendered and the complete text passes the text rules
   (:mod:`podex.sdk_log_rules`). If that changes it, the message becomes the
   sanitised text and the arguments are cleared. A record past the budget
   (message length, arguments, placeholders, characters inspected) becomes
   a fixed marker.
4. A logged traceback is rebuilt without source text or messages: file
   names, line numbers, function names and exception classes, with causes,
   contexts and exception group members. Cached traceback text that cannot
   be rebuilt is withheld, and stack text keeps its frame lines only. The
   original exception is detached from the record.

Records from other loggers are untouched.
"""

import contextlib
import logging
import re
import traceback
from collections.abc import Iterable, Mapping, Sized
from dataclasses import dataclass
from itertools import islice
from types import TracebackType

import httpx
import httpx2

from podex.database_errors import MAX_REBUILD_DEPTH, MAX_TREE_SIZE, placeholder
from podex.redaction import REDACTED
from podex.sdk_log_forms import known_form
from podex.sdk_log_rules import (
    MAX_ITEMS,
    Rendered,
    configure_secret_values,
    describe_exception,
    is_credential_key,
    is_oversized,
    message_object_skeleton,
    redact_configured,
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

MAX_ARGUMENTS = 64
"""Arguments of one record inspected; a record with more is withheld."""

MAX_PLACEHOLDERS = 64
"""Placeholders of one message inspected; a message with more is withheld."""

MAX_RECORD_TEXT = 32_768
"""Characters of string values inspected per record; the rest are withheld."""

MAX_FRAMES = 100
"""Frames printed per traceback; the rest are summarised."""

TRACEBACK_WITHHELD = "[traceback text withheld]"

_MAX_DEPTH = 4
_LABEL_TAIL = 96
_STACK_SCAN = 65_536
_SCALARS = (int, float, bool, type(None), type)
_UNHANDLED = object()
_FORMAT_RE = re.compile(
    r"%(?:\((?P<key>[^)]{0,128})\))?[#0 +\-]{0,4}(?P<width>\*|\d{1,4})?"
    r"(?:\.(?P<precision>\*|\d{1,4}))?[hlL]?(?P<conversion>[diouxXeEfFgGcrsa%])",
)
_LABEL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 _.\-]{0,63}?)[ \t]*[:=][ \t\r\n]*$")
_STACK_FRAME_RE = re.compile(
    r'^  File "[^"\n]{1,1024}", line \d{1,9}, in [^\n]{1,256}$',
    re.MULTILINE,
)


class _BudgetExceeded(Exception):
    """A record needs more inspection than its budget allows."""


@dataclass(slots=True)
class _Budget:
    """What one record may still inspect: container items and characters."""

    items: int = MAX_ITEMS
    characters: int = MAX_RECORD_TEXT


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
    characters before each placeholder are examined. ``%%`` is skipped. A
    ``*`` width or precision consumes an integer argument of its own, which
    gets an entry before its placeholder's.

    Raises:
        _BudgetExceeded: Past :data:`MAX_PLACEHOLDERS` placeholders.
    """
    found: list[tuple[str | None, str, str | None]] = []
    position = 0
    for match in _FORMAT_RE.finditer(message):
        conversion = match.group("conversion")
        for part in ("width", "precision"):
            if match.group(part) == "*":
                found.append((None, "d", None))
        if conversion != "%":
            tail = message[max(position, match.start() - _LABEL_TAIL) : match.start()]
            label = _LABEL_RE.search(tail)
            found.append(
                (match.group("key"), conversion, label.group(1) if label else None),
            )
        if len(found) > MAX_PLACEHOLDERS:
            raise _BudgetExceeded
        position = match.end()
    return found


def _limited(value: object) -> str:
    """Return the placeholder for a container past the inspection bound."""
    text: str = placeholder(value, reason="sanitiser limit reached")
    return text


def _over_budget(value: object, budget: _Budget) -> bool:
    """Return whether a sized container holds more items than remain."""
    size = 0
    if isinstance(value, Sized):
        with contextlib.suppress(Exception):
            size = len(value)
    return size > budget.items


def _safe_string(value: str, budget: _Budget) -> str:
    """Apply the text rules to a string within the record's budget.

    A string too long to inspect, or past the record's remaining characters,
    is withheld by type and size without being read.
    """
    if is_oversized(value) or len(value) > budget.characters:
        hidden: str = withheld(value)
        return hidden
    budget.characters -= len(value)
    cleaned: str = sanitise_text(value)
    return cleaned


def _safe_mapping(
    value: Mapping[object, object],
    depth: int,
    budget: _Budget,
) -> object:
    """Clean a mapping entry by entry, deciding each value by its key."""
    if _over_budget(value, budget):
        return _limited(value)
    cleaned: dict[object, object] = {}
    for key, item in islice(value.items(), budget.items + 1):
        budget.items -= 1
        if budget.items < 0:
            return _limited(value)
        cleaned[_safe_value(key, None, depth + 1, budget)] = _safe_value(
            item,
            key,
            depth + 1,
            budget,
        )
    return cleaned


def _safe_collection(value: Iterable[object], depth: int, budget: _Budget) -> object:
    """Clean each item of a sequence or set."""
    if _over_budget(value, budget):
        return _limited(value)
    items: list[object] = []
    for item in islice(value, budget.items + 1):
        budget.items -= 1
        if budget.items < 0:
            return _limited(value)
        items.append(_safe_value(item, None, depth + 1, budget))
    return tuple(items) if isinstance(value, tuple) else items


def _rendered(value: object, conversion: str, budget: _Budget) -> object:
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
    return Rendered(_safe_string(text, budget))


def _safe_scalar(value: object, budget: _Budget) -> object:
    """Clean a value whose type alone decides; :data:`_UNHANDLED` otherwise.

    Strings get the text rules; numbers, ``None`` and classes are kept;
    bytes are withheld; exceptions print their class; URLs lose credentials.
    """
    result: object = _UNHANDLED
    if isinstance(value, str):
        result = _safe_string(value, budget)
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
    budget: _Budget,
    conversion: str,
) -> object:
    """Clean a container or an arbitrary object.

    Containers are rebuilt from cleaned items, up to :data:`_MAX_DEPTH`
    levels and the record's item budget, without reading past it; request-
    and response-like objects print their structure; any other object is
    rendered with its placeholder's ``conversion`` and passes the text rules.
    """
    if depth >= _MAX_DEPTH:
        return _limited(value)
    if isinstance(value, Mapping):
        return _safe_mapping(value, depth, budget)
    if isinstance(value, list | tuple | set | frozenset):
        return _safe_collection(value, depth, budget)
    structured = message_object_skeleton(value)
    if structured is not None:
        return Rendered(structured)
    return _rendered(value, conversion, budget)


def _safe_named(value: object, kind: str | None) -> object:
    """Clean a value by its name alone; :data:`_UNHANDLED` otherwise.

    Credential-named values are masked and body-named ones withheld; a
    headers-named container keeps allowlisted values only, and an
    already-rendered header text is withheld.
    """
    result: object = _UNHANDLED
    if value is None or kind is None:
        return result
    if kind == "credential":
        result = REDACTED
    elif kind == "body" or isinstance(value, str | bytes | bytearray):
        result = withheld(value)
    else:
        result = safe_headers(value)
    return result


def _safe_value(
    value: object,
    label: object,
    depth: int,
    budget: _Budget,
    conversion: str = "r",
) -> object:
    """Return ``value`` as it may be logged, deciding by its key, then its type.

    A value named like a credential is masked and one named like a body is
    withheld; a value named like headers keeps only allowlisted header
    values, and an already-rendered header text is withheld. Objects are
    rendered with the placeholder's ``conversion`` (``repr`` in containers).
    """
    named = _safe_named(value, _label_kind(label))
    if named is not _UNHANDLED:
        return named
    scalar = _safe_scalar(value, budget)
    if scalar is not _UNHANDLED:
        return scalar
    if isinstance(value, httpx.Headers | httpx2.Headers):
        return safe_headers(value)
    return _safe_object(value, depth, budget, conversion)


def _frame_lines(trace: TracebackType | None) -> list[str]:
    """Return a traceback's frames: file, line and function, no source text."""
    lines: list[str] = []
    for count, (frame, line) in enumerate(traceback.walk_tb(trace)):
        if count >= MAX_FRAMES:
            lines.append("  [further frames withheld]")
            break
        code = frame.f_code
        lines.append(f'  File "{code.co_filename}", line {line}, in {code.co_name}')
    return lines


def _linked_lines(
    error: BaseException,
    seen: set[int],
    depth: int,
) -> list[str]:
    """Return the lines printed before ``error``: its cause or context."""
    cause = error.__cause__
    context = error.__context__
    if isinstance(cause, BaseException):
        return [
            *_exception_lines(cause, seen, depth + 1),
            "",
            "The above exception was the direct cause of the following exception:",
            "",
        ]
    if isinstance(context, BaseException) and not error.__suppress_context__:
        return [
            *_exception_lines(context, seen, depth + 1),
            "",
            "During handling of the above exception, another exception occurred:",
            "",
        ]
    return []


def _exception_lines(
    error: BaseException,
    seen: set[int],
    depth: int,
) -> list[str]:
    """Return the lines of one exception and what is printed with it.

    Each exception prints its frames and its class with the message
    withheld; causes and contexts first, exception group members after, up
    to :data:`MAX_REBUILD_DEPTH` links and :data:`MAX_TREE_SIZE` exceptions.
    """
    if id(error) in seen:
        return []
    if depth >= MAX_REBUILD_DEPTH or len(seen) >= MAX_TREE_SIZE:
        return [f"{describe_exception(error)} [further exceptions withheld]"]
    seen.add(id(error))
    lines = _linked_lines(error, seen, depth)
    group = isinstance(error, BaseExceptionGroup)
    if error.__traceback__ is not None:
        heading = "Exception Group Traceback" if group else "Traceback"
        lines.append(f"{heading} (most recent call last):")
        lines.extend(_frame_lines(error.__traceback__))
    if not isinstance(error, BaseExceptionGroup):
        lines.append(describe_exception(error))
        return lines
    members = error.exceptions
    plural = "" if len(members) == 1 else "s"
    lines.append(f"{describe_exception(error)} ({len(members)} sub-exception{plural})")
    for index, member in enumerate(members[:MAX_TREE_SIZE], start=1):
        lines.append(f"+---------------- {index} ----------------")
        lines.extend(
            f"| {line}" if line else "|"
            for line in _exception_lines(member, seen, depth + 1)
        )
    lines.append("+------------------------------------")
    return lines


def format_withheld_exception(error: BaseException) -> str:
    """Format a traceback without source text or exception messages.

    File names, line numbers, function names and exception classes print as
    :mod:`traceback` prints them, with causes, contexts and exception group
    members; source lines, messages and notes do not. The configured secret
    values are then replaced.

    Args:
        error: The exception logged by an SDK.

    Returns:
        str: The traceback text.
    """
    text: str = redact_configured("\n".join(_exception_lines(error, set(), 0)))
    return text


def rebuilt_stack(stack: str) -> str:
    """Keep only the frame lines of logged stack text.

    Args:
        stack: The ``stack_info`` text of a record.

    Returns:
        str: ``Stack (most recent call last):`` and each frame's file, line
            and function, without source text.
    """
    frames = _STACK_FRAME_RE.findall(stack[:_STACK_SCAN])[:MAX_FRAMES]
    text: str = redact_configured(
        "\n".join(["Stack (most recent call last):", *frames]),
    )
    return text


def _clean_arguments(
    message: str,
    arguments: object,
) -> tuple[object, ...] | dict[str, object]:
    """Return a record's arguments cleaned, in the shape formatting expects.

    Positional arguments stay a tuple of the same length; a mapping filling
    named placeholders stays a mapping with the same keys.

    Raises:
        _BudgetExceeded: Past :data:`MAX_ARGUMENTS` or the placeholder bound.
    """
    budget = _Budget()
    placeholders = _placeholders(message)
    if isinstance(arguments, Mapping) and any(key for key, _, _ in placeholders):
        if len(arguments) > MAX_ARGUMENTS:
            raise _BudgetExceeded
        conversions = {key: conversion for key, conversion, _ in placeholders}
        return {
            str(key): _safe_value(item, key, 1, budget, conversions.get(str(key), "s"))
            for key, item in arguments.items()
        }
    # A single mapping argument is stored as the arguments themselves.
    positional = arguments if isinstance(arguments, tuple) else (arguments,)
    if len(positional) > MAX_ARGUMENTS:
        raise _BudgetExceeded
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


def _withheld_record(record: logging.LogRecord, reason: str) -> None:
    """Replace a record's content with a fixed marker naming its size."""
    size = len(record.msg) if isinstance(record.msg, str) else 0
    record.msg = f"[{reason}, {size} characters]"
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None


def _cleaned_message(record: logging.LogRecord, text: str) -> None:
    """Clean an unrecognised record at INFO or above, within its budget."""
    if is_oversized(text):
        raise _BudgetExceeded
    if not record.args:
        record.msg, record.args = sanitise_text(text), ()
        return
    cleaned = _clean_arguments(text, record.args)
    rendered = text % cleaned
    if is_oversized(rendered):
        raise _BudgetExceeded
    safe = sanitise_text(rendered)
    if safe == rendered:
        record.msg, record.args = text, cleaned
    else:
        record.msg, record.args = safe, ()


def _cleaned_tracebacks(record: logging.LogRecord) -> None:
    """Rebuild the traceback and stack text, or withhold what cannot be."""
    exc_info = record.exc_info
    if isinstance(exc_info, tuple) and isinstance(exc_info[1], BaseException):
        record.exc_text = format_withheld_exception(exc_info[1])
    elif record.exc_text is not None:
        record.exc_text = TRACEBACK_WITHHELD
    record.exc_info = None
    if isinstance(record.stack_info, str):
        record.stack_info = rebuilt_stack(record.stack_info)
    elif record.stack_info is not None:
        record.stack_info = None


def sanitise_sdk_record(record: logging.LogRecord) -> None:
    """Withhold, skeletonise or clean one SDK record, by form and level.

    A known record form becomes its skeleton. Any other record below INFO is
    withheld. Any other record at INFO or above is cleaned within its budget,
    or withheld past it. Tracebacks and stack text are rebuilt without
    source text and messages. Raises on internal failure; the caller then
    replaces the whole record with a placeholder.

    Args:
        record: A record from a logger in :data:`SDK_LOGGER_FAMILIES`.
    """
    text = _message_text(record.msg)
    form = known_form(record.name, text, record.args)
    if form is not None:
        record.msg, record.args = form
    elif record.levelno < logging.INFO:
        _withheld_record(record, "debug record withheld: unrecognised form")
        return
    else:
        try:
            _cleaned_message(record, text)
        except _BudgetExceeded:
            _withheld_record(record, "record withheld: exceeds the inspection budget")
            return
    _cleaned_tracebacks(record)
