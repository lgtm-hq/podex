"""Last-resort removal of credentials from free text in error reports.

The reporter drops credentials by field name (headers, query strings, a
key denylist). Values inside free text, such as an exception message that
embeds a URL or a log line that renders an object, are not covered by name.
This module replaces, in any text:

- URL userinfo (``scheme://user:password@host`` becomes
  ``scheme://[redacted]@host``), and
- the known secret values of the loaded settings, raw and URL-encoded.

The secret values are held only inside a compiled pattern on a
:class:`SecretRedactor`, whose ``repr`` names no value. Values shorter than
:data:`MIN_REDACTED_SECRET_LENGTH` are ignored: they cannot be real
credentials, and matching them would redact unrelated text. Each text is
scanned once by one alternation pattern, so the cost is linear in the size
of the text.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urlsplit

from pydantic import BaseModel, SecretStr

MIN_REDACTED_SECRET_LENGTH = 8
"""Shorter secret values are not matched in free text."""

REDACTED = "[redacted]"

# Userinfo cannot contain "/", "?", "#" or whitespace, so a match stops at
# the authority; quotes and brackets end it too so that one URL in a quoted
# list cannot swallow the next item.
_URL_USERINFO_RE = re.compile(r"://[^\s/?#@\"'<>\\]*@")


def redact_url_userinfo(text: str) -> str:
    """Replace the userinfo of every URL in ``text``.

    Args:
        text: Free text that may contain URLs.

    Returns:
        str: The text with ``user:password@`` replaced by ``[redacted]@``.
    """
    return _URL_USERINFO_RE.sub(f"://{REDACTED}@", text)


def _credential_parts(value: str) -> Iterator[str]:
    """Yield the parts of one secret setting worth matching in free text.

    A URL-valued secret (a database or Redis URL) contributes only its
    password, raw and decoded: the rest of the URL is not secret, and its
    userinfo is removed by :func:`redact_url_userinfo` anyway. Any other
    secret contributes its whole value.

    Args:
        value: The secret's value.

    Yields:
        str: Each text form to redact.
    """
    if "://" not in value:
        yield value
        return
    try:
        password = urlsplit(value).password
    except ValueError:
        return
    if password:
        yield password
        yield unquote(password)


def secret_values(model: BaseModel) -> Iterator[str]:
    """Yield the value of every secret field in a settings model, recursively.

    Args:
        model: A settings model whose credentials are ``SecretStr`` fields.

    Yields:
        str: Each secret value (for URLs, only the password).
    """
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, SecretStr):
            yield from _credential_parts(value.get_secret_value())
        elif isinstance(value, BaseModel):
            yield from secret_values(value)


class SecretRedactor:
    """Replaces URL userinfo and configured secret values in text.

    The redactor holds the secrets only in its compiled pattern and never
    exposes them: ``repr`` names no value and nothing is logged.
    """

    __slots__ = ("_pattern",)

    def __init__(self) -> None:
        self._pattern: re.Pattern[str] | None = None

    def __repr__(self) -> str:
        """Describe the redactor without its secrets."""
        return f"{type(self).__name__}()"

    def configure(self, secrets: Iterable[str]) -> None:
        """Replace the set of secret values to redact.

        Args:
            secrets: Secret values; empty and short values are ignored.
        """
        variants = {
            variant
            for secret in secrets
            if len(secret) >= MIN_REDACTED_SECRET_LENGTH
            for variant in (secret, quote(secret, safe=""), quote_plus(secret))
        }
        # Longest first, so a value containing another is replaced whole.
        ordered = sorted(variants, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(re.escape(value) for value in ordered))
            if ordered
            else None
        )

    def redact(self, text: str) -> str:
        """Return ``text`` without URL userinfo or configured secret values.

        Args:
            text: Free text, such as an exception message or log line.

        Returns:
            str: The redacted text.
        """
        text = redact_url_userinfo(text)
        if self._pattern is None:
            return text
        return self._pattern.sub(REDACTED, text)


def redact_in_place(
    container: dict[Any, Any] | list[Any], redact: Callable[[str], str]
) -> None:
    """Apply ``redact`` to every string nested in a JSON-like structure.

    Mapping keys are kept; values are replaced in place. Each string is
    visited once.

    Args:
        container: A mapping or list, such as an event or a breadcrumb.
        redact: The text redaction to apply.
    """
    entries = container.items() if isinstance(container, dict) else enumerate(container)
    for key, value in entries:
        if isinstance(value, str):
            container[key] = redact(value)
        elif isinstance(value, dict | list):
            redact_in_place(value, redact)
