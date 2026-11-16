"""Structured logging configuration.

:func:`install_log_boundaries` installs the credential boundaries every log
record and uncaught exception passes; :func:`configure_logging` also sets the
application's log format. Every process entrypoint installs the boundaries: the API
(``create_app``), the scheduler runner and the migration environment.

- Records from third-party SDK loggers (the LLM SDK, the HTTP clients and
  their transports, the Spotify SDK, the object storage client, the ASGI
  server's access log) lose credentials, request and response bodies and
  error text, and keep method, host, path, status and exception classes
  (:mod:`podex.sdk_log_boundary`).
- Database errors are written by class and SQLSTATE only, wherever they are
  logged or printed (application code, the scheduler, the ASGI server,
  Alembic, uncaught exceptions): a driver's text can carry row values
  (``DETAIL:  Key (column)=(value) already exists.``).

Text the application formats into a string before logging it is outside
these boundaries; an SDK's own pre-formatted text passes the SDK
boundary's text rules.
"""

import contextlib
import logging
import sys
import threading
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
    Set,
)
from functools import partial
from types import TracebackType
from typing import Any

from podex.database_errors import (
    chain_has_database_error,
    exception_class_names,
    format_sanitised_exception,
    placeholder,
    stand_in,
    withheld_exception,
)
from podex.sdk_log_boundary import (
    configure_secret_values,
    is_sdk_logger,
    sanitise_sdk_record,
)

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_MAX_ARGUMENT_DEPTH = 4
_MAX_ARGUMENT_ITEMS = 1_000
_SCALAR_TYPES = frozenset({str, int, float, bool, bytes, type(None)})
_FAILED = object()

RecordFactory = Callable[..., logging.LogRecord]
"""``logging``'s record factory."""


def _limit(value: object) -> str:
    """Return the placeholder for a value past an inspection limit."""
    text: str = placeholder(value, reason="sanitiser limit reached")
    return text


def _clean_collection(
    value: Collection[object], depth: int, budget: list[int]
) -> object:
    """Clean a sequence or set's items; rebuild only if one changed."""
    items: list[object] = []
    changed = False
    for item in value:
        budget[0] -= 1
        if budget[0] < 0:
            return _limit(value)
        cleaned = _clean(item, depth + 1, budget)
        changed = changed or cleaned is not item
        items.append(cleaned)
    if not changed:
        return value
    return tuple(items) if isinstance(value, tuple) else items


def _clean_mapping(
    value: Mapping[object, object], depth: int, budget: list[int]
) -> object:
    """Clean a mapping's keys and values; rebuild only if one changed."""
    pairs: list[tuple[object, object]] = []
    changed = False
    for key, item in value.items():
        budget[0] -= 1
        if budget[0] < 0:
            return _limit(value)
        cleaned_key = _clean(key, depth + 1, budget)
        cleaned_item = _clean(item, depth + 1, budget)
        changed = changed or cleaned_key is not key or cleaned_item is not item
        pairs.append((cleaned_key, cleaned_item))
    return dict(pairs) if changed else value


def _clean(value: object, depth: int, budget: list[int]) -> object:
    """Return ``value`` with database errors withheld, or ``value`` itself.

    Formatting renders containers' items with ``repr``, so sequences, sets
    and mappings are searched; a container is rebuilt only when an item
    changed. An exception whose printed tree holds a database error
    (including an exception group) becomes a sanitised stand-in. Past
    :data:`_MAX_ARGUMENT_DEPTH` levels or :data:`_MAX_ARGUMENT_ITEMS` items, a
    container becomes a placeholder; if inspecting it fails, too.
    """
    if type(value) in _SCALAR_TYPES:
        return value
    if isinstance(value, BaseException):
        return withheld_exception(value) if chain_has_database_error(value) else value
    is_mapping = isinstance(value, Mapping)
    is_collection = isinstance(value, Sequence | Set) and not isinstance(
        value, str | bytes | bytearray
    )
    if not (is_mapping or is_collection):
        return value
    if depth >= _MAX_ARGUMENT_DEPTH:
        return _limit(value)
    cleaned: object = _FAILED
    with contextlib.suppress(Exception):
        if isinstance(value, Mapping):
            cleaned = _clean_mapping(value, depth, budget)
        elif isinstance(value, Sequence | Set):
            cleaned = _clean_collection(value, depth, budget)
    return placeholder(value) if cleaned is _FAILED else cleaned


def sanitise_database_errors(record: logging.LogRecord) -> None:
    """Withhold database error text from one log record.

    A logged traceback whose printed tree (chain and exception groups) holds
    a database error is formatted without that error's message, replacing
    any text cached earlier; formatters use the cached ``exc_text``, and
    ``exc_info`` is kept, so error reporting still sees the exception. A
    database error (or an exception group holding one) used as the message,
    as a log argument, as a mapping argument's key or value or nested in a
    container argument is replaced by a sanitised stand-in.

    Args:
        record: The record, edited in place.
    """
    exc_info = record.exc_info
    if (
        isinstance(exc_info, tuple)
        and isinstance(exc_info[1], BaseException)
        and chain_has_database_error(exc_info[1])
    ):
        record.exc_text = format_sanitised_exception(exc_info[1])
    budget = [_MAX_ARGUMENT_ITEMS]
    record.msg = _clean(record.msg, 0, budget)
    arguments = record.args
    if isinstance(arguments, tuple):
        cleaned: list[object] = []
        for argument in arguments:
            budget[0] -= 1
            cleaned.append(
                _clean(argument, 1, budget) if budget[0] >= 0 else _limit(argument)
            )
        if any(new is not old for new, old in zip(cleaned, arguments, strict=True)):
            record.args = tuple(cleaned)
    elif isinstance(arguments, Mapping):
        mapping = _clean(arguments, 0, budget)
        if isinstance(mapping, Mapping):
            record.args = mapping
        else:
            record.msg, record.args = mapping, ()


def _replace_with_placeholder(record: logging.LogRecord) -> None:
    """Replace a record's content after sanitising it failed.

    The message, arguments and traceback text become placeholders naming the
    message type and the exception classes; nothing original is printed.
    """
    error = record.exc_info[1] if isinstance(record.exc_info, tuple) else None
    classes = exception_class_names(error) if isinstance(error, BaseException) else []
    record.msg = placeholder(record.msg, classes=classes)
    record.args = ()
    if isinstance(error, BaseException):
        record.exc_text = placeholder(error, classes=classes)


def _sanitising_record(
    inner: RecordFactory, *args: Any, **kwargs: Any
) -> logging.LogRecord:
    """Create a record with the inner factory, then apply the boundaries.

    Database text is withheld from every record; a record from a
    third-party SDK logger also loses credentials, bodies and error text.
    Never raises from sanitising: on any failure the record's content is
    replaced by placeholders instead, and an SDK record's exception is
    detached.
    """
    record: logging.LogRecord = inner(*args, **kwargs)
    from_sdk = is_sdk_logger(record.name)
    sanitised = False
    with contextlib.suppress(Exception):
        sanitise_database_errors(record)
        if from_sdk:
            sanitise_sdk_record(record)
        sanitised = True
    if not sanitised:
        _replace_with_placeholder(record)
        if from_sdk:
            record.exc_info = None
    return record


def _write_stderr(text: str) -> None:
    """Write a line to standard error, if there is one; never raises."""
    with contextlib.suppress(Exception):
        if sys.stderr is not None:
            sys.stderr.write(text + "\n")


def _shown(error: BaseException) -> BaseException:
    """Return the exception to pass on: ``error`` or its sanitised stand-in."""
    shown: BaseException | None = None
    with contextlib.suppress(Exception):
        shown = withheld_exception(error) if chain_has_database_error(error) else error
    if shown is None:
        shown = stand_in(
            error, placeholder(error, classes=exception_class_names(error))
        )
    return shown


def _sanitising_excepthook(
    previous: Callable[..., object],
    kind: type[BaseException],
    error: BaseException,
    trace: TracebackType | None,
) -> None:
    """Pass an uncaught exception on without database error text.

    The previous hook (the interpreter's printer, or an error reporter's
    hook) receives a sanitised stand-in tree when the exception holds a
    database error, so it still prints and reports. Never raises: if the
    previous hook fails, a placeholder naming the classes is printed.
    """
    shown = _shown(error) if isinstance(error, BaseException) else error
    delivered = False
    with contextlib.suppress(Exception):
        if shown is error:
            previous(kind, error, trace)
        else:
            previous(type(shown), shown, shown.__traceback__)
        delivered = True
    if not delivered:
        _write_stderr(placeholder(error, classes=exception_class_names(shown)))


def _sanitising_thread_excepthook(
    previous: Callable[[threading.ExceptHookArgs], object],
    args: threading.ExceptHookArgs,
) -> None:
    """Pass an uncaught thread exception on without database error text."""
    error = args.exc_value
    shown = _shown(error) if isinstance(error, BaseException) else error
    delivered = False
    with contextlib.suppress(Exception):
        if shown is not error and isinstance(shown, BaseException):
            args = threading.ExceptHookArgs(
                [type(shown), shown, shown.__traceback__, args.thread],
            )
        previous(args)
        delivered = True
    if not delivered:
        _write_stderr(placeholder(shown))


def _wraps(installed: object, wrapper: Callable[..., object]) -> bool:
    """Return whether ``installed`` is ``wrapper`` bound to a previous hook."""
    return (
        isinstance(installed, partial) and getattr(installed, "func", None) is wrapper
    )


def _install_database_error_boundary() -> None:
    """Sanitise every record and uncaught exception, whatever emits it, once."""
    factory = logging.getLogRecordFactory()
    if not _wraps(factory, _sanitising_record):
        logging.setLogRecordFactory(partial(_sanitising_record, factory))
    if not _wraps(sys.excepthook, _sanitising_excepthook):
        sys.excepthook = partial(_sanitising_excepthook, sys.excepthook)
    if not _wraps(threading.excepthook, _sanitising_thread_excepthook):
        threading.excepthook = partial(
            _sanitising_thread_excepthook,
            threading.excepthook,
        )


def install_log_boundaries() -> None:
    """Install the credential boundaries without changing log output.

    Every process entrypoint calls this (the API and scheduler through
    :func:`configure_logging`, the migration environment directly), so the
    boundaries hold whichever process logs or fails. They live in the log
    record factory and the exception hooks, which logging configuration
    (``basicConfig``, ``fileConfig``, ``dictConfig``) does not replace, and
    are installed once however often this is called.
    """
    _install_database_error_boundary()


def redact_secret_values(secrets: Iterable[str]) -> None:
    """Also replace configured secret values in third-party SDK records.

    Each process entrypoint that loads settings calls this with their secret
    values; until then, SDK records are cleaned by structure, by key and
    for URL userinfo only. A later call replaces the values.

    Args:
        secrets: Secret values of the loaded settings; empty values are ignored.
    """
    configure_secret_values(secrets)


def configure_logging(*, level: str = "INFO") -> None:
    """Configure root logging once for the application."""
    logging.basicConfig(level=level, format=_LOG_FORMAT)
    install_log_boundaries()


def get_logger(name: str) -> logging.Logger:
    """Return a named application logger."""
    return logging.getLogger(name)
