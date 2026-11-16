"""Credential boundary for log records written by third-party libraries.

The HTTP and SDK clients the application uses, and the libraries around
them, log on their own loggers (:data:`SDK_LOGGER_FAMILIES`). Their records
are allowlisted by form at every level. Every record from one of these
loggers passes :func:`sanitise_sdk_record` when it is created:

1. A known record form (:mod:`podex.sdk_log_forms`) is printed as its
   skeleton, built from validated parts only, within an output bound. The
   configured secret values are then replaced in the finished skeleton.
2. Any other record keeps its logger name and level, and its message
   becomes ``[record withheld: unrecognised form, N characters]``, followed
   by the validated class names of an attached exception. Its arguments,
   traceback text and stack text are dropped. It is neither rendered nor
   traversed.
3. A known form's traceback is rebuilt without source text or messages: file
   names, line numbers, function names and exception classes, with causes,
   contexts and exception group members. Cached traceback text that cannot
   be rebuilt is withheld, and stack text keeps its frame lines only. The
   original exception is detached from the record.

Records from other loggers are untouched.
"""

import logging
import re
import traceback
from types import TracebackType

from podex.database_errors import MAX_REBUILD_DEPTH, MAX_TREE_SIZE, exception_tree
from podex.sdk_log_forms import known_form
from podex.sdk_log_rules import (
    Rendered,
    class_name,
    configure_secret_values,
    describe_exception,
    redact_configured,
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
    "charset_normalizer",
    "spotipy",
    "boto3",
    "botocore",
    "s3transfer",
    "sentry_sdk",
    "pydantic_settings",
    "bs4",
    "uvicorn.access",
)
"""Loggers (with their children) whose records pass the SDK boundary."""

MAX_SKELETON = 8_192
"""Longest printed skeleton; a longer one is withheld."""

MAX_FRAMES = 100
"""Frames printed per traceback; the rest are summarised."""

MAX_CLASSES = 8
"""Exception class names named by a withheld record's marker."""

TRACEBACK_WITHHELD = "[traceback text withheld]"

_STACK_SCAN = 65_536
_NUMERIC_PLACEHOLDER_RE = re.compile(r"%(?:\.\d+)?[dif]")
_STACK_FRAME_RE = re.compile(
    r'^  File "[^"\n]{1,1024}", line \d{1,9}, in [^\n]{1,256}$',
    re.MULTILINE,
)


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

    Each exception prints its frames and its validated class with the
    message withheld; causes and contexts first, exception group members
    after, up to :data:`MAX_REBUILD_DEPTH` links and :data:`MAX_TREE_SIZE`
    exceptions.
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

    File names, line numbers, function names and validated exception classes
    print as :mod:`traceback` prints them, with causes, contexts and
    exception group members; source lines, messages and notes do not. The
    configured secret values are then replaced.

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


def _exception_classes(record: logging.LogRecord) -> list[str]:
    """Return the validated class names of a record's attached exception."""
    exc_info = record.exc_info
    if not (isinstance(exc_info, tuple) and isinstance(exc_info[1], BaseException)):
        return []
    return [class_name(link) for link in exception_tree(exc_info[1])[:MAX_CLASSES]]


def _withheld_record(record: logging.LogRecord, reason: str) -> None:
    """Replace a record's content with a fixed marker naming its size."""
    size = len(record.msg) if isinstance(record.msg, str) else 0
    classes = _exception_classes(record)
    suffix = f"; exception {', '.join(classes)}" if classes else ""
    record.msg = f"[record withheld: {reason}, {size} characters{suffix}]"
    record.args = ()
    record.exc_info = None
    record.exc_text = None
    record.stack_info = None


def _finished(
    message: str,
    arguments: tuple[object, ...],
) -> tuple[str, tuple[object, ...]]:
    """Replace configured secret values in every text part of a skeleton.

    A format string is a literal of :mod:`podex.sdk_log_forms` and is kept
    as it is, so its placeholders stay intact; a pre-formatted skeleton (no
    arguments) and every text argument pass the redactor.
    """
    if not arguments:
        return redact_configured(message), ()
    cleaned = tuple(
        Rendered(redact_configured(part)) if isinstance(part, str) else part
        for part in arguments
    )
    return message, cleaned


def _printable_format(message: str, arguments: tuple[object, ...]) -> str:
    """Return a known form's format string that prints its validated parts.

    A numeric placeholder whose part could not be validated, and is printed
    as ``[withheld]``, becomes ``%s``; the rest of the literal is unchanged.
    """
    if not arguments:
        return message
    try:
        _ = message % arguments
    except (TypeError, ValueError):
        return _NUMERIC_PLACEHOLDER_RE.sub("%s", message)
    return message


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
    """Print a known form's skeleton, or withhold the record whole.

    A known record form becomes its skeleton, built from validated parts and
    bounded by :data:`MAX_SKELETON`; any other record, at any level, becomes
    a fixed marker. Tracebacks and stack text of known forms are rebuilt
    without source text and messages. Raises on internal failure; the caller
    then replaces the whole record with a placeholder.

    Args:
        record: A record from a logger in :data:`SDK_LOGGER_FAMILIES`.
    """
    form = known_form(record.name, record.msg, record.args)
    if form is None:
        _withheld_record(record, "unrecognised form")
        return
    message, arguments = _finished(*form)
    message = _printable_format(message, arguments)
    if len(message % arguments if arguments else message) > MAX_SKELETON:
        _withheld_record(record, "skeleton exceeds the output bound")
        return
    record.msg, record.args = message, arguments
    _cleaned_tracebacks(record)
