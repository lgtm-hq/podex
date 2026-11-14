"""Database errors described without the driver's message.

A driver's error text can carry row values: PostgreSQL renders a unique
violation as ``DETAIL:  Key (column)=(value) already exists.``, whatever
``hide_parameters`` hides. Wherever such an error would be written as free
text, it is replaced by an allowlisted descriptor: the exception classes and,
where the driver provides one, the SQLSTATE code or SQLite error name.

Exceptions are traversed as the interpreter prints them: through the
explicit cause, the unsuppressed context, and the members of exception
groups.

Everything here is fail-safe: no function raises, and none falls back to
the original text. Conversions that fail, and walks that reach a limit,
produce a fixed placeholder (see :func:`placeholder`) naming the type that
could not be printed; a walk that stops early assumes a database error may
lie beyond it.
"""

import contextlib
import re
import sqlite3
import traceback
from collections import deque
from collections.abc import Iterable

import psycopg
from sqlalchemy.exc import StatementError

DATABASE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    StatementError,
    psycopg.Error,
    sqlite3.Error,
)
"""Errors whose message can render statement parameters or row values."""

WITHHELD = "[driver message withheld]"

MAX_TREE_SIZE = 256
"""Exceptions inspected per printed tree; beyond it, a database error is assumed."""

MAX_REBUILD_DEPTH = 64
"""Links followed when rebuilding a tree; deeper links become a placeholder."""

_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
_SQLITE_ERROR_NAME_RE = re.compile(r"SQLITE_[A-Z_]{1,40}")
_STAND_INS: dict[tuple[type[BaseException], type[BaseException]], type] = {}
_STAND_IN_ORIGINS: dict[type, type[BaseException]] = {}


def class_name(kind: type) -> str:
    """Return a class's name; never raises."""
    name = getattr(kind, "__name__", None)
    return name if isinstance(name, str) else "object"


def type_name(value: object) -> str:
    """Return the name of ``value``'s class; never raises."""
    return class_name(type(value))


def placeholder(
    value: object,
    *,
    reason: str = "sanitiser failed",
    classes: Iterable[str] = (),
) -> str:
    """Return the fixed text printed in place of content that cannot be shown.

    Args:
        value: The object that could not be printed; only its class is named.
        reason: Why: the sanitiser failed, or a traversal limit was reached.
        classes: Exception class names to keep as a trace, if any.

    Returns:
        str: For example ``"<unprintable: sanitiser failed on BadValue>"``.
    """
    names = ", ".join(classes)
    suffix = f" [{names}]" if names else ""
    return f"<unprintable: {reason} on {type_name(value)}>{suffix}"


def safe_str(value: object) -> str:
    """Return ``str(value)``, or a placeholder if conversion fails."""
    text: str | None = None
    with contextlib.suppress(Exception):
        converted = str(value)
        if isinstance(converted, str):
            text = converted
    return text if text is not None else placeholder(value)


def is_database_error(value: object) -> bool:
    """Return whether ``value`` is a database error whose text is withheld.

    Args:
        value: Any object, typically an exception or a log argument.

    Returns:
        bool: True for SQLAlchemy statement errors and DBAPI driver errors.
    """
    return isinstance(value, DATABASE_ERROR_TYPES)


def _driver_code(error: BaseException) -> str | None:
    """Return the driver's SQLSTATE or SQLite error name, if well formed."""
    code: str | None = None
    with contextlib.suppress(Exception):
        sqlstate = getattr(error, "sqlstate", None)
        name = getattr(error, "sqlite_errorname", None)
        if isinstance(sqlstate, str) and _SQLSTATE_RE.fullmatch(sqlstate):
            code = f"SQLSTATE {sqlstate}"
        elif isinstance(name, str) and _SQLITE_ERROR_NAME_RE.fullmatch(name):
            code = name
    return code


def describe_database_error(error: BaseException) -> str:
    """Describe a database error by class and code only.

    Args:
        error: A SQLAlchemy statement error or a DBAPI driver error.

    Returns:
        str: For example ``"IntegrityError (UniqueViolation, SQLSTATE 23505)"``.
    """
    driver: object = error
    if isinstance(error, StatementError):
        driver = None
        with contextlib.suppress(Exception):
            driver = error.orig
    details: list[str] = []
    if isinstance(driver, BaseException):
        if driver is not error:
            details.append(type_name(driver))
        code = _driver_code(driver)
        if code is not None:
            details.append(code)
    name = type_name(error)
    return f"{name} ({', '.join(details)})" if details else name


def _linked(error: BaseException) -> list[BaseException]:
    """Return the exceptions printed with ``error``: group members, then chain."""
    linked: list[BaseException] = []
    if isinstance(error, BaseExceptionGroup):
        linked.extend(
            member for member in error.exceptions if isinstance(member, BaseException)
        )
    if isinstance(error.__cause__, BaseException):
        linked.append(error.__cause__)
    elif (
        isinstance(error.__context__, BaseException) and not error.__suppress_context__
    ):
        linked.append(error.__context__)
    return linked


def _walk(error: BaseException) -> tuple[list[BaseException], bool]:
    """Return the printed tree breadth first, and whether the walk was cut short."""
    found: dict[int, BaseException] = {id(error): error}
    frontier = deque([error])
    while frontier:
        for linked in _linked(frontier.popleft()):
            if id(linked) in found:
                continue
            if len(found) >= MAX_TREE_SIZE:
                return list(found.values()), True
            found[id(linked)] = linked
            frontier.append(linked)
    return list(found.values()), False


def exception_tree(error: BaseException) -> list[BaseException]:
    """Return ``error`` and the exceptions printed with it, each once.

    Follows ``__cause__``, the unsuppressed ``__context__`` and exception
    group members, breadth first, up to :data:`MAX_TREE_SIZE` exceptions.

    Args:
        error: The exception being logged or reported.

    Returns:
        list[BaseException]: The exceptions found; empty if the walk failed.
    """
    tree: list[BaseException] = []
    with contextlib.suppress(Exception):
        tree = _walk(error)[0]
    return tree


def exception_class_names(error: BaseException) -> list[str]:
    """Return the class names of ``error``'s printed tree, as a trace.

    Args:
        error: The exception being logged or reported.

    Returns:
        list[str]: Class names in walk order; at least ``error``'s own.
    """
    return [type_name(link) for link in exception_tree(error)] or [type_name(error)]


def chain_has_database_error(error: BaseException) -> bool:
    """Return whether a database error may be printed with ``error``.

    A walk that fails or reaches :data:`MAX_TREE_SIZE` answers True, so the
    caller withholds rather than prints.

    Args:
        error: The exception being logged or reported.

    Returns:
        bool: True when a database error is, or may be, in the printed tree.
    """
    answer = True
    with contextlib.suppress(Exception):
        tree, truncated = _walk(error)
        answer = truncated or any(is_database_error(link) for link in tree)
    return answer


def _stand_in_type(
    kind: type[BaseException],
    base: type[BaseException],
) -> type:
    """Return an exception class printed with ``kind``'s module and name."""
    key = (kind, base)
    created = _STAND_INS.get(key)
    if created is None:
        name = class_name(kind)
        module = getattr(kind, "__module__", "builtins")
        qualname = getattr(kind, "__qualname__", name)
        created = type(
            name,
            (base,),
            {
                "__module__": module if isinstance(module, str) else "builtins",
                "__qualname__": qualname if isinstance(qualname, str) else name,
                "__doc__": "Stand-in printed in place of the original exception.",
            },
        )
        _STAND_INS[key] = created
        _STAND_IN_ORIGINS[created] = kind
    return created


def is_database_stand_in(value: object) -> bool:
    """Return whether ``value`` is a stand-in printed in place of a database error.

    Its text is already sanitised: a descriptor or a placeholder.

    Args:
        value: Any object, typically an exception from a rebuilt tree.

    Returns:
        bool: True for stand-ins of database error classes.
    """
    origin = _STAND_IN_ORIGINS.get(type(value))
    return origin is not None and issubclass(origin, DATABASE_ERROR_TYPES)


def stand_in(error: BaseException, message: str) -> BaseException:
    """Return a plain exception printing ``error``'s class with ``message``.

    Args:
        error: The exception whose module and class name are printed.
        message: The text printed in place of its message.

    Returns:
        BaseException: The stand-in, without traceback or links.
    """
    replacement: BaseException = _stand_in_type(type(error), Exception)(message)
    return replacement


def _rebuilt(
    error: BaseException,
    memo: dict[int, BaseException],
    depth: int,
) -> BaseException:
    """Return ``error`` with every database error message withheld.

    Exceptions whose printed tree holds no database error are returned as
    they are. Others are rebuilt as stand-ins that print the same module,
    class name and message, with the same traceback, links and notes; a
    database error's message is replaced by its descriptor. Past
    :data:`MAX_REBUILD_DEPTH` links, or :data:`MAX_TREE_SIZE` rebuilt
    exceptions, the rest is a placeholder.
    """
    known = memo.get(id(error))
    if known is not None:
        return known
    if not chain_has_database_error(error):
        memo[id(error)] = error
        return error
    if depth >= MAX_REBUILD_DEPTH or len(memo) >= MAX_TREE_SIZE:
        limit = stand_in(error, placeholder(error, reason="sanitiser limit reached"))
        memo[id(error)] = limit
        return limit
    if is_database_error(error):
        copy = stand_in(error, f"{describe_database_error(error)} {WITHHELD}")
        memo[id(error)] = copy
    elif isinstance(error, BaseExceptionGroup):
        message = safe_str(error.message)
        # Provisional entry: a member linking back to this group meets a
        # stand-in that holds no database text.
        memo[id(error)] = stand_in(error, message)
        members = [_rebuilt(member, memo, depth + 1) for member in error.exceptions]
        base: type[BaseException] = (
            ExceptionGroup
            if all(isinstance(member, Exception) for member in members)
            else BaseExceptionGroup
        )
        copy = _stand_in_type(type(error), base)(message, members)
        memo[id(error)] = copy
    else:
        copy = stand_in(error, safe_str(error))
        memo[id(error)] = copy
    notes = getattr(error, "__notes__", None)
    if not is_database_error(error) and isinstance(notes, list):
        copy.__notes__ = [safe_str(note) for note in notes]
    copy.__traceback__ = error.__traceback__
    if isinstance(error.__cause__, BaseException):
        copy.__cause__ = _rebuilt(error.__cause__, memo, depth + 1)
    if isinstance(error.__context__, BaseException) and not error.__suppress_context__:
        copy.__context__ = _rebuilt(error.__context__, memo, depth + 1)
    copy.__suppress_context__ = error.__suppress_context__
    return copy


def withheld_exception(error: BaseException) -> BaseException:
    """Return an exception that prints like ``error`` without database text.

    Never raises: if the rebuild fails, a stand-in carrying a placeholder and
    the tree's class names is returned instead.

    Args:
        error: The exception to print or report.

    Returns:
        BaseException: ``error`` itself when it holds no database error;
            otherwise a rebuilt tree of stand-ins.
    """
    rebuilt: BaseException | None = None
    with contextlib.suppress(Exception):
        rebuilt = _rebuilt(error, {}, 0)
    if rebuilt is not None:
        return rebuilt
    failed = stand_in(error, placeholder(error, classes=exception_class_names(error)))
    failed.__traceback__ = error.__traceback__
    return failed


def format_sanitised_exception(error: BaseException) -> str:
    """Format a traceback like :mod:`traceback`, without database error text.

    Frames, exception groups, notes and other exceptions are printed as
    usual; each database error's message (statement, parameters, driver
    ``DETAIL``) is replaced by :func:`describe_database_error`. Never raises:
    if formatting fails, a placeholder with the tree's class names is
    returned instead.

    Args:
        error: The exception to format.

    Returns:
        str: The traceback text, without a trailing newline.
    """
    text: str | None = None
    with contextlib.suppress(Exception):
        printed = withheld_exception(error)
        text = "".join(traceback.format_exception(printed)).rstrip("\n")
    if text is not None:
        return text
    return placeholder(error, classes=exception_class_names(error))
