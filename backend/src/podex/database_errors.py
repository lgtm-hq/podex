"""Database errors described without the driver's message.

A driver's error text can carry row values: PostgreSQL renders a unique
violation as ``DETAIL:  Key (column)=(value) already exists.``, whatever
``hide_parameters`` hides. Wherever such an error would be written as free
text, it is replaced by an allowlisted descriptor: the exception classes and,
where the driver provides one, the SQLSTATE code or SQLite error name.

Exceptions are traversed as the interpreter prints them: through the
explicit cause, the unsuppressed context, and the members of exception
groups, recursively.
"""

import re
import sqlite3
import traceback
from collections import deque

import psycopg
from sqlalchemy.exc import StatementError

DATABASE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    StatementError,
    psycopg.Error,
    sqlite3.Error,
)
"""Errors whose message can render statement parameters or row values."""

WITHHELD = "[driver message withheld]"

_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
_SQLITE_ERROR_NAME_RE = re.compile(r"SQLITE_[A-Z_]{1,40}")
_STAND_INS: dict[tuple[type[BaseException], type[BaseException]], type] = {}


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
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and _SQLSTATE_RE.fullmatch(sqlstate):
        return f"SQLSTATE {sqlstate}"
    name = getattr(error, "sqlite_errorname", None)
    if isinstance(name, str) and _SQLITE_ERROR_NAME_RE.fullmatch(name):
        return name
    return None


def describe_database_error(error: BaseException) -> str:
    """Describe a database error by class and code only.

    Args:
        error: A SQLAlchemy statement error or a DBAPI driver error.

    Returns:
        str: For example ``"IntegrityError (UniqueViolation, SQLSTATE 23505)"``.
    """
    driver = (
        getattr(error, "orig", None) if isinstance(error, StatementError) else error
    )
    details: list[str] = []
    if isinstance(driver, BaseException):
        if driver is not error:
            details.append(type(driver).__name__)
        code = _driver_code(driver)
        if code is not None:
            details.append(code)
    name = type(error).__name__
    return f"{name} ({', '.join(details)})" if details else name


def _linked(error: BaseException) -> list[BaseException]:
    """Return the exceptions printed with ``error``: group members, then chain."""
    linked: list[BaseException] = []
    if isinstance(error, BaseExceptionGroup):
        linked.extend(error.exceptions)
    if error.__cause__ is not None:
        linked.append(error.__cause__)
    elif error.__context__ is not None and not error.__suppress_context__:
        linked.append(error.__context__)
    return linked


def exception_tree(error: BaseException) -> list[BaseException]:
    """Return ``error`` and every exception printed with it, each once.

    Follows ``__cause__``, the unsuppressed ``__context__`` and exception
    group members, breadth first; an exception reached twice (a cycle, or a
    member shared by groups) is listed once.

    Args:
        error: The exception being logged or reported.

    Returns:
        list[BaseException]: Each exception the interpreter would print.
    """
    found: dict[int, BaseException] = {id(error): error}
    frontier = deque([error])
    while frontier:
        for linked in _linked(frontier.popleft()):
            if id(linked) not in found:
                found[id(linked)] = linked
                frontier.append(linked)
    return list(found.values())


def chain_has_database_error(error: BaseException) -> bool:
    """Return whether any exception printed with ``error`` is a database error.

    Args:
        error: The exception being logged or reported.

    Returns:
        bool: True when a database error's text would be printed.
    """
    return any(is_database_error(link) for link in exception_tree(error))


def _stand_in_type(
    kind: type[BaseException],
    base: type[BaseException],
) -> type:
    """Return an exception class printed with ``kind``'s module and name."""
    key = (kind, base)
    stand_in = _STAND_INS.get(key)
    if stand_in is None:
        stand_in = type(
            kind.__name__,
            (base,),
            {
                "__module__": kind.__module__,
                "__qualname__": kind.__qualname__,
                "__doc__": "Stand-in printed in place of the original exception.",
            },
        )
        _STAND_INS[key] = stand_in
    return stand_in


def _rebuilt(error: BaseException, memo: dict[int, BaseException]) -> BaseException:
    """Return ``error`` with every database error message withheld.

    Exceptions whose printed tree holds no database error are returned as
    they are. Others are rebuilt as stand-ins that print the same module,
    class name and message, with the same traceback, links and notes; a
    database error's message is replaced by its descriptor.
    """
    known = memo.get(id(error))
    if known is not None:
        return known
    if not chain_has_database_error(error):
        memo[id(error)] = error
        return error
    kind = type(error)
    if is_database_error(error):
        copy: BaseException = _stand_in_type(kind, Exception)(
            f"{describe_database_error(error)} {WITHHELD}",
        )
        memo[id(error)] = copy
    elif isinstance(error, BaseExceptionGroup):
        # Provisional entry: a member linking back to this group meets a
        # stand-in that holds no database text.
        memo[id(error)] = _stand_in_type(kind, Exception)(error.message)
        members = [_rebuilt(member, memo) for member in error.exceptions]
        base: type[BaseException] = (
            ExceptionGroup
            if all(isinstance(member, Exception) for member in members)
            else BaseExceptionGroup
        )
        copy = _stand_in_type(kind, base)(error.message, members)
        memo[id(error)] = copy
    else:
        copy = _stand_in_type(kind, Exception)(str(error))
        memo[id(error)] = copy
    if not is_database_error(error) and hasattr(error, "__notes__"):
        copy.__notes__ = list(error.__notes__)
    copy.__traceback__ = error.__traceback__
    if error.__cause__ is not None:
        copy.__cause__ = _rebuilt(error.__cause__, memo)
    if error.__context__ is not None:
        copy.__context__ = _rebuilt(error.__context__, memo)
    copy.__suppress_context__ = error.__suppress_context__
    return copy


def withheld_exception(error: BaseException) -> BaseException:
    """Return an exception that prints like ``error`` without database text.

    Args:
        error: The exception to print.

    Returns:
        BaseException: ``error`` itself when it holds no database error;
            otherwise a rebuilt tree of stand-ins.
    """
    return _rebuilt(error, {})


def format_sanitised_exception(error: BaseException) -> str:
    """Format a traceback like :mod:`traceback`, without database error text.

    Frames, exception groups, notes and other exceptions are printed as
    usual; each database error's message (statement, parameters, driver
    ``DETAIL``) is replaced by :func:`describe_database_error`.

    Args:
        error: The exception to format.

    Returns:
        str: The traceback text, without a trailing newline.
    """
    printed = withheld_exception(error)
    return "".join(traceback.format_exception(printed)).rstrip("\n")
