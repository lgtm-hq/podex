"""Report an unusable database URL without any part of it.

Parsing a database URL, loading its dialect and driver and building an engine
can fail with text that quotes the URL. This module has no import-time side
effects, so the migration environment can use it without building the
application engine.
"""

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import SQLAlchemyError

ResultT = TypeVar("ResultT")

# What parsing a URL, loading its dialect and driver, and building an engine
# raise for an unusable URL. Some quote part of the URL: SQLAlchemy reads an
# unescaped "@" or ":" in a password as the end of the userinfo and then fails
# on the rest of the password as a port number.
_URL_FAILURES = (SQLAlchemyError, ImportError, LookupError, TypeError, ValueError)


class DatabaseUrlError(RuntimeError):
    """An unusable database URL, reported without any part of the URL."""


def without_url_text(build: Callable[[], ResultT]) -> ResultT:
    """Run ``build`` (parsing a database URL or building an engine from one).

    Args:
        build: Callable that parses or uses the configured database URL.

    Returns:
        What ``build`` returns.

    Raises:
        DatabaseUrlError: When ``build`` fails as an unusable URL does; the
            message names the failure's class only, and the original error is
            neither chained nor kept as context, so the URL's text (which can
            hold a password) is printed nowhere.
    """
    failure = ""
    try:
        return build()
    except _URL_FAILURES as error:
        failure = type(error).__name__
    # Outside the handler: even a suppressed exception context holds the URL.
    raise DatabaseUrlError(
        f"The database URL (PODEX_DATABASE__URL) is not usable: {failure}. "
        "Its text is withheld because it can hold a password.",
    )
