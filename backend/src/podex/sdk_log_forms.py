"""Known third-party SDK record forms and the skeleton each keeps.

Some SDK records carry provider-supplied free text, or put credentials where
the general rules of :mod:`podex.sdk_log_boundary` cannot see them by
structure. For these forms the record keeps a skeleton (method, sanitised
URL, numeric status, parameter names, allowlisted headers, class names) and
withholds the rest:

- The Spotify SDK's pre-formatted lines: ``RESULTS: <response body>``, the
  ``HTTP Error for ... due to <provider message>`` line (ERROR), and the
  ``Sending ...`` request lines with headers and body.
- The HTTP clients' request line: the provider's reason phrase is replaced
  by the standard phrase for the status.
- The ASGI server's access line: arguments stay a five-item tuple, which its
  access formatter unpacks; the request target loses credential query
  values.
- The LLM SDK's standalone header records (``request_id: %s``,
  ``workspace_id: %s``): the header allowlist applies.
- The storage client's signing dumps (``CanonicalRequest``,
  ``StringToSign``): header lines keep allowlisted values only and query
  lines lose credential values.
"""

import re

from podex.redaction import REDACTED
from podex.sdk_log_rules import (
    cap_text,
    header_value,
    is_credential_key,
    is_safe_header,
    masked_query,
    reason_phrase,
    safe_headers,
    safe_url,
    sanitise_text,
)

HTTP_REQUEST_LINE = 'HTTP Request: %s %s "%s %d %s"'
"""The HTTP clients' (``httpx``, ``httpx2``) request line."""

ACCESS_LINE = '%s - "%s %s HTTP/%s" %d'
"""The ASGI server's access line."""

STANDALONE_HEADERS = {
    "request_id: %s": "request-id",
    "workspace_id: %s": "anthropic-workspace-id",
}
"""The LLM SDK's records that log one response header, by message."""

SIGNING_DUMPS = frozenset(
    {"CanonicalRequest:\n%s", "StringToSign:\n%s", "String to sign: %s"},
)
"""The storage client's signing input records."""

BODY_WITHHELD = "[withheld]"
PROVIDER_MESSAGE_WITHHELD = "[provider message withheld]"

_BOUND = 4_096
_SPOTIFY_ERROR_RE = re.compile(
    r"HTTP Error for (?P<method>[A-Z]{1,16}) to (?P<url>\S{1,4096}) with Params: "
    r"(?P<params>.{0,4096}?) returned (?P<status>\d{3}) due to ",
    re.DOTALL,
)
_SPOTIFY_REQUEST_RE = re.compile(
    r"Sending (?P<method>[A-Z]{1,16}) to (?P<url>\S{1,4096}) with Params: "
    r"(?P<params>.{0,4096}?) Headers: (?P<headers>\{.{0,4096}?\}) and Body: ",
    re.DOTALL,
)
_SPOTIFY_TOKEN_RE = re.compile(
    r"Sending POST request to (?P<url>\S{1,4096}) with Headers: "
    r"(?P<headers>\{.{0,4096}?\}) and Body: ",
    re.DOTALL,
)
_SPOTIFY_RESULTS = "RESULTS: "
_QUOTED_KEY_RE = re.compile(r"""['"]([^'"\\]{1,128})['"][ ]?:""")
_HEADER_ENTRY_RE = re.compile(r"""'([^'\\]{1,128})': '((?:[^'\\]|\\.){0,4096})'""")
_HEADER_LINE_RE = re.compile(r"([!#$%&'*+.^_`|~0-9A-Za-z\-]{1,128}):(.*)")


def _family(name: str, family: str) -> bool:
    """Return whether logger ``name`` is ``family`` or one of its children."""
    return name == family or name.startswith(f"{family}.")


def _parameter_names(params: str) -> str:
    """Render a logged parameter mapping as its names, values withheld."""
    names = _QUOTED_KEY_RE.findall(params[:_BOUND])[:50]
    return repr(
        {name: REDACTED if is_credential_key(name) else BODY_WITHHELD for name in names}
    )


def _spotify_headers(rendered: str) -> object:
    """Parse a rendered header mapping and apply the header allowlist."""
    pairs = _HEADER_ENTRY_RE.findall(rendered[:_BOUND])
    return safe_headers(pairs)


def _spotify_line(message: str) -> str | None:
    """Return the skeleton of a known Spotify SDK line, or None."""
    if message.startswith(_SPOTIFY_RESULTS):
        size = len(message) - len(_SPOTIFY_RESULTS)
        return f"{_SPOTIFY_RESULTS}[withheld response body ({size} characters)]"
    error = _SPOTIFY_ERROR_RE.match(message)
    if error is not None:
        return (
            f"HTTP Error for {error['method']} to {safe_url(error['url'])} with "
            f"Params: {_parameter_names(error['params'])} returned "
            f"{error['status']} due to {PROVIDER_MESSAGE_WITHHELD}"
        )
    request = _SPOTIFY_REQUEST_RE.match(message)
    if request is not None:
        return (
            f"Sending {request['method']} to {safe_url(request['url'])} with Params: "
            f"{_parameter_names(request['params'])} Headers: "
            f"{_spotify_headers(request['headers'])} and Body: {BODY_WITHHELD}"
        )
    token = _SPOTIFY_TOKEN_RE.match(message)
    if token is not None:
        return (
            f"Sending POST request to {safe_url(token['url'])} with Headers: "
            f"{_spotify_headers(token['headers'])} and Body: {BODY_WITHHELD}"
        )
    return None


def _access_line(arguments: tuple[object, ...]) -> tuple[object, ...] | None:
    """Clean the access line's five arguments, keeping the tuple."""
    client, method, target, version, status = arguments
    if not isinstance(status, int) or not all(
        isinstance(part, str) for part in (client, method, target, version)
    ):
        return None
    return (
        sanitise_text(str(client)),
        sanitise_text(str(method)),
        safe_url(str(target)),
        sanitise_text(str(version)),
        status,
    )


def fixed_form(
    name: str,
    message: str,
    arguments: object,
) -> tuple[str, tuple[object, ...]] | None:
    """Return the cleaned message and arguments of a fixed record form.

    Fixed forms are final: their message is not rendered and re-scanned,
    either because it is already a skeleton (the Spotify SDK's lines) or
    because the formatter needs the argument tuple (the access line).

    Args:
        name: The logger name.
        message: The record's message.
        arguments: The record's arguments.

    Returns:
        tuple | None: ``(message, arguments)``, or None for other records.
    """
    if _family(name, "spotipy") and not arguments:
        skeleton = _spotify_line(message)
        return None if skeleton is None else (skeleton, ())
    if (
        name == "uvicorn.access"
        and message == ACCESS_LINE
        and isinstance(arguments, tuple)
        and len(arguments) == 5
    ):
        cleaned = _access_line(arguments)
        return None if cleaned is None else (message, cleaned)
    return None


def safe_signing_text(text: str) -> str:
    """Apply the header allowlist and query rules to a signing dump.

    Args:
        text: A canonical request or string to sign.

    Returns:
        str: Each ``name:value`` line with the value kept only for allowlisted
            headers; each query line without credential values.
    """
    lines: list[str] = []
    for line in cap_text(text).split("\n"):
        header = _HEADER_LINE_RE.fullmatch(line)
        if header is not None:
            shown = header[2] if is_safe_header(header[1]) else REDACTED
            lines.append(f"{header[1]}:{shown}")
        elif "=" in line and " " not in line:
            lines.append(masked_query(line))
        else:
            lines.append(line)
    return "\n".join(lines)


def prepare_arguments(name: str, message: str, arguments: object) -> object:
    """Apply a record form's rule to its arguments before general cleaning.

    Args:
        name: The logger name.
        message: The record's message.
        arguments: The record's arguments.

    Returns:
        object: The arguments, with the form's rule applied if one matches.
    """
    if not isinstance(arguments, tuple) or not arguments:
        return arguments
    if (
        (name in ("httpx", "httpx2"))
        and message == HTTP_REQUEST_LINE
        and len(arguments) == 5
    ):
        return (*arguments[:4], reason_phrase(arguments[3]))
    if _family(name, "anthropic") and message in STANDALONE_HEADERS:
        header = STANDALONE_HEADERS[message]
        return (header_value(header, arguments[0]), *arguments[1:])
    if (
        _family(name, "botocore")
        and message in SIGNING_DUMPS
        and isinstance(arguments[0], str)
    ):
        return (safe_signing_text(arguments[0]), *arguments[1:])
    return arguments
