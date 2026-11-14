"""Last-resort removal of credentials from free text in error reports.

The reporter drops credentials by field name (headers, query strings, a
key denylist). Values inside free text, such as an exception message that
embeds a URL or a log line that renders an object, are not covered by name.
This module replaces, in one pass over each text:

- URL userinfo (``scheme://user:password@host`` becomes
  ``scheme://[redacted]@host``), and
- the known secret values of the loaded settings, raw and percent-encoded
  (each escape's hex digits in either case).

Every non-empty secret is matched, however short: redacting unrelated text
that happens to equal a short secret is preferred to sending the secret.
Because the text is scanned once, a replacement is never scanned again.
The secret values are held only inside a compiled pattern on a
:class:`SecretRedactor`, whose ``repr`` names no value. The cost is linear
in the size of the text.

Known limits: JSON-escaped, base64 or case-changed forms of a secret are not
matched, and the secrets are those of the settings the reporter was
initialised with.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit

from pydantic import BaseModel, SecretStr

from podex.database_errors import placeholder

SHORT_VALUE_LENGTH = 8
"""Configured values shorter than this are still redacted, but flagged."""

URL_SECRET_FIELDS = frozenset({"database.url", "rate_limit.redis_url"})
"""Settings holding a URL; their userinfo password and credential options
are matched as values, not the whole URL."""

URL_CREDENTIAL_OPTIONS = frozenset({"password", "sslpassword", "ssl_password"})
"""Query options that carry a credential: ``password`` (libpq and Redis),
``sslpassword`` (libpq client key passphrase) and ``ssl_password`` (Redis
client key passphrase)."""

REDACTED = "[redacted]"

# RFC 3986 userinfo: unreserved, percent-encoded, sub-delims and ":". A
# match must be followed by a host, so "@" in a path or query never counts.
_USERINFO = r"://(?P<userinfo>[A-Za-z0-9\-._~!$&'()*+,;=:%]*)@(?=[A-Za-z0-9\[])"
_USERINFO_RE = re.compile(_USERINFO)
_SCHEME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-",
)
_MAX_SCHEME_LENGTH = 32
_LIST_SEPARATORS = frozenset(",;)]}")
_MAX_DATA_DEPTH = 16
_PERCENT_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})")


def _opening_quote(text: str, scheme_end: int) -> str | None:
    """Return the quote character directly before a URL's scheme, if any."""
    position = scheme_end
    limit = max(0, scheme_end - _MAX_SCHEME_LENGTH)
    while position > limit and text[position - 1] in _SCHEME_CHARS:
        position -= 1
    if position > 0 and text[position - 1] in "'\"":
        return text[position - 1]
    return None


def _is_userinfo(match: re.Match[str]) -> bool:
    """Return whether a userinfo candidate stays inside its URL.

    A URL opened with a quote and followed by a list separator after that
    same quote (``'https://h.test','a@b'``) ends there, so a candidate
    spanning it covers neighbouring text and is not userinfo. A quote
    followed by anything else is part of the userinfo: an apostrophe in a
    password (``'https://reader:pa'ss@host/rss'``) is redacted.
    """
    quote_char = _opening_quote(match.string, match.start())
    if quote_char is None:
        return True
    userinfo = match.group("userinfo")
    position = userinfo.find(quote_char)
    while position != -1:
        following = userinfo[position + 1 : position + 2]
        if following and following in _LIST_SEPARATORS:
            return False
        position = userinfo.find(quote_char, position + 1)
    return True


def _replace_userinfo(match: re.Match[str]) -> str:
    """Redact a URL's userinfo, leaving a declined candidate unchanged."""
    return f"://{REDACTED}@" if _is_userinfo(match) else match.group(0)


def redact_url_userinfo(text: str) -> str:
    """Replace the userinfo of every URL in ``text``.

    Args:
        text: Free text that may contain URLs.

    Returns:
        str: The text with ``user:password@`` replaced by ``[redacted]@``.
    """
    return _USERINFO_RE.sub(_replace_userinfo, text)


def _url_credentials(url: str) -> Iterator[str]:
    """Yield a URL setting's credentials: password and credential options.

    The rest of the URL is not secret, and its userinfo is removed by
    :func:`redact_url_userinfo` anyway. A URL that cannot be parsed
    contributes its whole value instead.

    Args:
        url: The URL setting's value.

    Yields:
        str: Each credential, raw and decoded.
    """
    try:
        parts = urlsplit(url)
        password = parts.password
    except ValueError:
        yield url
        return
    if password:
        yield password
        yield unquote(password)
    for name, value in parse_qsl(parts.query):
        if name.lower() in URL_CREDENTIAL_OPTIONS and value:
            yield value


def _field_values(path: str, value: SecretStr) -> Iterator[str]:
    """Yield the values of one masked setting to match in free text."""
    raw = value.get_secret_value()
    if not raw:
        return
    if path in URL_SECRET_FIELDS:
        yield from _url_credentials(raw)
    else:
        yield raw


def secret_values(model: BaseModel, prefix: str = "") -> Iterator[str]:
    """Yield every value to redact from a settings model, recursively.

    Masked (``SecretStr``) fields contribute their whole value; a field in
    :data:`URL_SECRET_FIELDS` contributes its URL's credentials instead.
    Empty values (a disabled feature) contribute nothing.

    Args:
        model: A settings model.
        prefix: Dotted path of ``model`` within the root settings.

    Yields:
        str: Each value to redact.
    """
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        value = getattr(model, name)
        if isinstance(value, SecretStr):
            yield from _field_values(path, value)
        elif isinstance(value, BaseModel):
            yield from secret_values(value, f"{path}.")


def _is_undersized(path: str, value: SecretStr) -> bool:
    """Return whether any value of a masked setting is under 8 characters."""
    return any(len(part) < SHORT_VALUE_LENGTH for part in _field_values(path, value))


def undersized_field_paths(model: BaseModel, prefix: str = "") -> list[str]:
    """Return the dotted paths of configured masked settings that are short.

    Only paths are returned, built from field names; the length check is
    made here so that nothing derived from a value leaves this function.

    Args:
        model: A settings model.
        prefix: Dotted path of ``model`` within the root settings.

    Returns:
        list[str]: Paths of settings with a value shorter than 8 characters.
    """
    paths: list[str] = []
    for name in type(model).model_fields:
        path = f"{prefix}{name}"
        value = getattr(model, name)
        if isinstance(value, SecretStr):
            if _is_undersized(path, value):
                paths.append(path)
        elif isinstance(value, BaseModel):
            paths.extend(undersized_field_paths(value, f"{path}."))
    return paths


def _hex_case_insensitive(match: re.Match[str]) -> str:
    """Return a pattern for one percent-escape with either hex-digit case."""
    digits = "".join(
        f"[{digit.upper()}{digit.lower()}]" if digit.isalpha() else digit
        for digit in match.group(1)
    )
    return f"%{digits}"


def _form_pattern(form: str) -> str:
    """Return a pattern for ``form`` whose escapes match in any hex case.

    Literal characters keep their case; only the two hex digits of each
    ``%XX`` escape are matched case-insensitively, each independently.
    """
    parts: list[str] = []
    position = 0
    for match in _PERCENT_ESCAPE_RE.finditer(form):
        parts.append(re.escape(form[position : match.start()]))
        parts.append(_hex_case_insensitive(match))
        position = match.end()
    parts.append(re.escape(form[position:]))
    return "".join(parts)


def _encodable(value: str) -> bool:
    """Return whether ``value`` has a UTF-8 encoding (no lone surrogates)."""
    valid = True
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        valid = False
    return valid


def _forms(secret: str) -> set[str]:
    """Return a secret's raw and percent-encoded forms.

    A value without a UTF-8 encoding cannot be percent-encoded; its raw form
    is still matched.
    """
    if not _encodable(secret):
        return {secret}
    return {secret, quote(secret, safe=""), quote_plus(secret)}


class SecretRedactor:
    """Replaces URL userinfo and configured secret values in text.

    The redactor holds the secrets only in its compiled pattern and never
    exposes them: ``repr`` names no value and nothing is logged.
    """

    __slots__ = ("_pattern",)

    def __init__(self) -> None:
        self._pattern: re.Pattern[str] = _USERINFO_RE

    def __repr__(self) -> str:
        """Describe the redactor without its secrets."""
        return f"{type(self).__name__}()"

    def configure(self, secrets: Iterable[str]) -> None:
        """Replace the set of secret values to redact.

        Args:
            secrets: Secret values; empty values are ignored.
        """
        forms = {form for secret in secrets if secret for form in _forms(secret)}
        # Longest first, so a value containing another is replaced whole.
        alternatives = [
            _form_pattern(form) for form in sorted(forms, key=len, reverse=True)
        ]
        self._pattern = re.compile("|".join([_USERINFO, *alternatives]))

    def redact(self, text: str) -> str:
        """Return ``text`` without URL userinfo or configured secret values.

        Args:
            text: Free text, such as an exception message or log line.

        Returns:
            str: The redacted text.
        """
        return self._pattern.sub(self._replace, text)

    def _replace(self, match: re.Match[str]) -> str:
        """Return the replacement for one userinfo or secret-value match.

        A declined userinfo candidate is not URL userinfo, but the text it
        spans is still searched for secret values.
        """
        userinfo = match.group("userinfo")
        if userinfo is None:
            return REDACTED
        if _is_userinfo(match):
            return f"://{REDACTED}@"
        return f"://{self._pattern.sub(self._replace, userinfo)}@"


def _redacted(value: object, redact: Callable[[str], str], depth: int) -> object:
    """Return ``value`` redacted: strings rewritten, containers traversed.

    Tuples and sets become lists (the reporter serialises them as lists
    anyway); past :data:`_MAX_DATA_DEPTH` levels a container becomes a
    placeholder.
    """
    if isinstance(value, str):
        return redact(value)
    if not isinstance(value, dict | list | tuple | set | frozenset):
        return value
    if depth >= _MAX_DATA_DEPTH:
        return placeholder(value, reason="sanitiser limit reached")
    if isinstance(value, dict | list):
        redact_in_place(value, redact, depth)
        return value
    return [_redacted(item, redact, depth + 1) for item in value]


def redact_in_place(
    container: dict[Any, Any] | list[Any],
    redact: Callable[[str], str],
    depth: int = 0,
) -> None:
    """Apply ``redact`` to every string value nested in a JSON-like structure.

    Mapping keys are kept; values are replaced in place, including inside
    tuples and sets, which become lists. Each string is visited once.

    Args:
        container: A mapping or list, such as an event field or breadcrumb.
        redact: The text redaction to apply.
        depth: Nesting level of ``container``; deeper levels are bounded.
    """
    entries = (
        list(container.items())
        if isinstance(container, dict)
        else list(enumerate(container))
    )
    for key, value in entries:
        replaced = _redacted(value, redact, depth + 1)
        if replaced is not value:
            container[key] = replaced
