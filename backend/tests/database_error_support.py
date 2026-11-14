"""A PostgreSQL unique violation built without a database server.

Shared by in-process tests and the fresh-process entrypoint tests, which
import this module from a child interpreter.
"""

import sys

import psycopg.errors
from sqlalchemy.exc import IntegrityError

SQLSTATE_DESCRIPTOR = "IntegrityError (UniqueViolation, SQLSTATE 23505)"
"""How the error below is described wherever its text is withheld."""


def unique_violation(row_value: str) -> IntegrityError:
    """Return a SQLAlchemy error wrapping a unique violation on ``row_value``.

    The driver message carries the row value in its ``DETAIL`` line, as
    PostgreSQL renders it. The error is raised and caught, so it has
    tracebacks and the driver error as its cause, exactly as SQLAlchemy
    raises it.

    Args:
        row_value: The duplicate value the driver message reveals.

    Returns:
        IntegrityError: The wrapped error.
    """
    driver = psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "credentials_value_key"\n'
        f"DETAIL:  Key (value)=({row_value}) already exists.",
    )
    try:
        try:
            raise driver
        except psycopg.errors.UniqueViolation as error:
            raise IntegrityError(
                "INSERT INTO credentials (value) VALUES (?)",
                {"value": row_value},
                error,
                hide_parameters=True,
            ) from error
    except IntegrityError as wrapped:
        return wrapped


def print_uncaught(error: BaseException) -> None:
    """Print ``error`` as the interpreter prints an exception that ends a process.

    A spawned test process's runner prints exceptions escaping its target
    with its own code, so child processes call the interpreter's hook as it
    would be called when the error ends the process.

    Args:
        error: The exception ending the process.
    """
    sys.excepthook(type(error), error, error.__traceback__)
    sys.stderr.flush()


FOREIGN_KEY_DESCRIPTOR = "IntegrityError (ForeignKeyViolation, SQLSTATE 23503)"
"""How :func:`foreign_key_violation`'s error is described."""


def foreign_key_violation(row_value: str) -> IntegrityError:
    """Return a SQLAlchemy error wrapping a foreign key violation on ``row_value``.

    Args:
        row_value: The missing referenced value the driver message reveals.

    Returns:
        IntegrityError: The wrapped error, raised and caught like
            :func:`unique_violation`'s.
    """
    driver = psycopg.errors.ForeignKeyViolation(
        'insert or update violates foreign key constraint "credentials_owner_fkey"\n'
        f'DETAIL:  Key (owner)=({row_value}) is not present in table "owners".',
    )
    try:
        try:
            raise driver
        except psycopg.errors.ForeignKeyViolation as error:
            raise IntegrityError(
                "INSERT INTO credentials (owner) VALUES (?)",
                {"owner": row_value},
                error,
                hide_parameters=True,
            ) from error
    except IntegrityError as wrapped:
        return wrapped
