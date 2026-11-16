"""Report a configured credential-bearing value that cannot be parsed.

Parsing a database URL (and loading its dialect and driver), a Redis URL or an
error-reporting DSN can fail with text that quotes the value: a password whose
unescaped ``@``, ``:``, ``/``, ``#`` or ``?`` moves the rest of it into the
port, a query option or the project. Each such value is therefore parsed on
its own inside :func:`without_setting_text`, which reports a failure of the
parser's input-error classes by the setting's name and the error class only.
Clients and engines are then built from the validated value outside it, so a
fault in their initialisation surfaces unchanged. This module has no
import-time side effects, so the migration environment can use it without
building the application engine.
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from podex.sdk_log_rules import class_name

ResultT = TypeVar("ResultT")

# What parsing a database URL (``ValueError`` for a non-numeric port,
# ``ArgumentError`` for an unparsable URL or an unknown dialect) and importing
# its driver raise for bad input.
DATABASE_URL_FAILURES: tuple[type[Exception], ...] = (
    ArgumentError,
    ImportError,
    ValueError,
)

# Driver modules the application can need: PostgreSQL's psycopg (with its
# optional compiled implementations) and SQLite's standard-library module. A
# missing one is named; any other module name is omitted.
_DRIVER_MODULES = frozenset({"psycopg", "psycopg_binary", "psycopg_c", "sqlite3"})


class UnusableSettingError(RuntimeError):
    """A configured value that cannot be parsed, reported without any part of it."""


def _describe(error: BaseException) -> str:
    """Return the error's validated class and, if allowlisted, a missing driver."""
    described: str = class_name(error)
    module = error.name if isinstance(error, ImportError) else None
    if module in _DRIVER_MODULES:
        described += f" (module {module})"
    return described


def without_setting_text(
    parse: Callable[[], ResultT],
    *,
    source: str,
    failures: tuple[type[Exception], ...],
) -> ResultT:
    """Run ``parse``, which parses one configured value.

    Args:
        parse: Callable that parses the value with its library's own parser.
        source: Where the value comes from, such as its environment variable.
        failures: The classes that parser raises for bad input; anything else
            propagates unchanged.

    Returns:
        What ``parse`` returns.

    Raises:
        UnusableSettingError: When ``parse`` raises one of ``failures``; the
            message names ``source``, the failure's validated class and, for a
            missing driver the application can need, its module. The original
            error is neither chained nor kept as context, so the value (which
            can hold a credential) is printed nowhere.
    """
    failure = ""
    try:
        return parse()
    except failures as error:
        failure = _describe(error)
    # Outside the handler: even a suppressed exception context holds the value.
    raise UnusableSettingError(
        f"{source} is not usable: {failure}. "
        "Its text is withheld because it can hold a credential.",
    )


def checked_database_url(url: str, *, source: str, load_driver: bool) -> URL:
    """Parse a database URL and load its dialect (and, if asked, its driver).

    Args:
        url: The configured URL.
        source: Where it comes from, for the error.
        load_driver: Whether to import the driver too; generating SQL offline
            needs only the dialect.

    Returns:
        URL: The parsed URL, for building the engine.
    """

    def parse() -> URL:
        """Parse the URL, then load what it names."""
        parsed: URL = make_url(url)
        dialect = parsed.get_dialect()
        if load_driver:
            dialect.import_dbapi()
        return parsed

    return without_setting_text(parse, source=source, failures=DATABASE_URL_FAILURES)
