"""Database errors described without the driver's message.

A driver's error text can carry row values: PostgreSQL renders a unique
violation as ``DETAIL:  Key (column)=(value) already exists.``, whatever
``hide_parameters`` hides. Wherever such an error would be written as free
text, it is replaced by an allowlisted descriptor: the exception classes and,
where the driver provides one, the SQLSTATE code or SQLite error name.
"""

import re
import sqlite3
import traceback
from collections.abc import Iterator

import psycopg
from sqlalchemy.exc import StatementError

DATABASE_ERROR_TYPES: tuple[type[BaseException], ...] = (
    StatementError,
    psycopg.Error,
    sqlite3.Error,
)
"""Errors whose message can render statement parameters or row values."""

_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
_SQLITE_ERROR_NAME_RE = re.compile(r"SQLITE_[A-Z_]{1,40}")
_CAUSE = "\nThe above exception was the direct cause of the following exception:\n\n"
_CONTEXT = "\nDuring handling of the above exception, another exception occurred:\n\n"


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


def _chain(error: BaseException) -> Iterator[tuple[BaseException, str]]:
    """Yield each exception of a chain, newest first, with its link text.

    Follows ``__cause__`` and unsuppressed ``__context__`` as the interpreter
    does; the link text precedes the newer exception when printed.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if current.__cause__ is not None:
            yield current, _CAUSE
            current = current.__cause__
        elif current.__context__ is not None and not current.__suppress_context__:
            yield current, _CONTEXT
            current = current.__context__
        else:
            yield current, ""
            current = None


def exception_chain(error: BaseException) -> Iterator[BaseException]:
    """Yield ``error`` and the exceptions it was raised from, newest first.

    Args:
        error: The exception being logged or reported.

    Yields:
        BaseException: Each exception the interpreter would print.
    """
    for link, _text in _chain(error):
        yield link


def chain_has_database_error(error: BaseException) -> bool:
    """Return whether any exception in ``error``'s chain is a database error.

    Args:
        error: The exception being logged or reported.

    Returns:
        bool: True when a database error's text would be printed.
    """
    return any(is_database_error(link) for link in exception_chain(error))


def _summary(error: BaseException) -> str:
    """Return the final traceback line, withholding database error text."""
    if not is_database_error(error):
        return "".join(traceback.format_exception_only(error))
    kind = type(error)
    return (
        f"{kind.__module__}.{kind.__qualname__}: "
        f"{describe_database_error(error)} [driver message withheld]\n"
    )


def format_sanitised_exception(error: BaseException) -> str:
    """Format a traceback like :mod:`traceback`, without database error text.

    Frames and other exceptions are printed as usual; each database error's
    message (statement, parameters, driver ``DETAIL``) is replaced by
    :func:`describe_database_error`.

    Args:
        error: The exception to format.

    Returns:
        str: The traceback text, without a trailing newline.
    """
    parts: list[str] = []
    # Oldest first; each newer exception is preceded by the text linking it
    # to the one printed before it.
    for link, text in reversed(list(_chain(error))):
        parts.append(text)
        if link.__traceback__ is not None:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(traceback.format_tb(link.__traceback__))
        parts.append(_summary(link))
    return "".join(parts).rstrip("\n")
