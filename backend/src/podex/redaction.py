"""Last-resort removal of credentials from free text in error reports.

The reporter drops credentials by field name (headers, query strings, a
key denylist). Values inside free text, such as an exception message that
embeds a URL or a log line that renders an object, are not covered by name.
This module replaces, in any text:

- URL userinfo (``scheme://user:password@host`` becomes
  ``scheme://[redacted]@host``), and
- the known secret values of the loaded settings, raw and percent-encoded
  (upper- and lowercase escapes).

Every non-empty secret is matched, however short: redacting unrelated text
that happens to equal a short secret is preferred to sending the secret.
The secret values are held only inside a compiled pattern on a
:class:`SecretRedactor`, whose ``repr`` names no value. Each text is scanned
once by one alternation pattern, so the cost is linear in the size of the
text.

Known limits: JSON-escaped, base64 or case-changed forms of a secret are not
matched, and the secrets are those of the settings the reporter was
initialised with.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urlsplit

from pydantic import BaseModel, SecretStr

SHORT_SECRET_LENGTH = 8
"""Secrets shorter than this are still redacted, but flagged at startup."""

URL_SECRET_FIELDS = frozenset({"database.url", "rate_limit.redis_url"})
"""Secret settings holding a URL; only their password is matched as a value."""

REDACTED = "[redacted]"

# Userinfo cannot contain "/", "?", "#" or whitespace, so a match stops at
# the authority; quotes and brackets end it too so that one URL in a quoted
# list cannot swallow the next item.
_URL_USERINFO_RE = re.compile(r"://[^\s/?#@\"'<>\\]*@")
_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-F]{2}")


def redact_url_userinfo(text: str) -> str:
    """Replace the userinfo of every URL in ``text``.

    Args:
        text: Free text that may contain URLs.

    Returns:
        str: The text with ``user:password@`` replaced by ``[redacted]@``.
    """
    return _URL_USERINFO_RE.sub(f"://{REDACTED}@", text)


def _url_password(url: str) -> Iterator[str]:
    """Yield a URL setting's password, raw and decoded.

    The rest of the URL is not secret, and its userinfo is removed by
    :func:`redact_url_userinfo` anyway. A URL that cannot be parsed
    contributes its whole value instead.

    Args:
        url: The URL setting's value.

    Yields:
        str: Each form of the password to redact.
    """
    try:
        password = urlsplit(url).password
    except ValueError:
        yield url
        return
    if password:
        yield password
        yield unquote(password)


def secret_fields(model: BaseModel, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield the path and redactable value of every configured secret.

    Secret settings are ``SecretStr`` fields, found recursively. Empty
    values (a disabled feature) are skipped. A field in
    :data:`URL_SECRET_FIELDS` contributes its URL's password; every other
    secret contributes its whole value, whatever it contains.

    Args:
        model: A settings model.
        prefix: Dotted path of ``model`` within the root settings.

    Yields:
        tuple[str, str]: The field's dotted path and a value to redact.
    """
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        value = getattr(model, name)
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            if not secret:
                continue
            values = _url_password(secret) if path in URL_SECRET_FIELDS else (secret,)
            for redactable in values:
                yield path, redactable
        elif isinstance(value, BaseModel):
            yield from secret_fields(value, f"{path}.")


def secret_values(model: BaseModel) -> Iterator[str]:
    """Yield every redactable secret value of a settings model.

    Args:
        model: A settings model whose credentials are ``SecretStr`` fields.

    Yields:
        str: Each secret value (for URL settings, the password).
    """
    for _path, value in secret_fields(model):
        yield value


def short_secret_paths(model: BaseModel) -> list[str]:
    """Return the paths of configured secrets shorter than 8 characters.

    Args:
        model: A settings model.

    Returns:
        list[str]: Dotted field paths, each once, in declaration order.
    """
    paths = [
        path for path, value in secret_fields(model) if len(value) < SHORT_SECRET_LENGTH
    ]
    return list(dict.fromkeys(paths))


def _encoded_forms(secret: str) -> set[str]:
    """Return a secret's raw and percent-encoded forms, both escape cases."""
    forms = {secret, quote(secret, safe=""), quote_plus(secret)}
    lowered = {
        _PERCENT_ESCAPE_RE.sub(lambda match: match.group(0).lower(), form)
        for form in forms
    }
    return forms | lowered


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
            secrets: Secret values; empty values are ignored.
        """
        variants = {
            variant
            for secret in secrets
            if secret
            for variant in _encoded_forms(secret)
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
