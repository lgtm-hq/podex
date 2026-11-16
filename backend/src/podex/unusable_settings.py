"""Report a configured value that cannot be used without any part of it.

Parsing a database URL (and loading its dialect and driver), a Redis URL or an
error-reporting DSN can fail with text that quotes the value: a password whose
unescaped ``@``, ``:``, ``/``, ``#`` or ``?`` moves the rest of it into the
port, a query option or the project. A configured value is therefore built
inside :func:`without_setting_text`, which reports a failure by the setting's
name and the error class only. This module has no import-time side effects,
so the migration environment can use it without building the application
engine.
"""

import re
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import SQLAlchemyError

ResultT = TypeVar("ResultT")

# What parsing a URL or DSN, loading a dialect or driver, and building a client
# or engine raise for an unusable value.
_FAILURES = (SQLAlchemyError, ImportError, LookupError, TypeError, ValueError)

# A module the import system could not load: its dotted name, never a value.
_MODULE_NAME = re.compile(r"[A-Za-z_]\w{0,63}(?:\.[A-Za-z_]\w{0,63}){0,7}")


class UnusableSettingError(RuntimeError):
    """A configured value that cannot be used, reported without any part of it."""


def _describe(error: BaseException) -> str:
    """Return the error's class and, for a failed import, the module's name."""
    described = type(error).__name__
    module = error.name if isinstance(error, ImportError) else None
    if isinstance(module, str) and _MODULE_NAME.fullmatch(module):
        described += f" (module {module})"
    return described


def without_setting_text(build: Callable[[], ResultT], *, source: str) -> ResultT:
    """Run ``build``, which parses or uses one configured value.

    Args:
        build: Callable that parses the value or builds a client from it.
        source: Where the value comes from, such as its environment variable.

    Returns:
        What ``build`` returns.

    Raises:
        UnusableSettingError: When ``build`` fails as an unusable value does;
            the message names ``source``, the failure's class and, for a failed
            import, the missing module. The original error is neither chained
            nor kept as context, so the value (which can hold a credential) is
            printed nowhere.
    """
    failure = ""
    try:
        return build()
    except _FAILURES as error:
        failure = _describe(error)
    # Outside the handler: even a suppressed exception context holds the value.
    raise UnusableSettingError(
        f"{source} is not usable: {failure}. "
        "Its text is withheld because it can hold a credential.",
    )
