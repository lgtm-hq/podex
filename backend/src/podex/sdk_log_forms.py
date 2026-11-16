"""Known third-party SDK record forms and the skeleton each keeps.

Third-party DEBUG output is allowlisted by form: a record below INFO from a
covered SDK is written with content only if it is one of the forms here,
and each form's handler builds a skeleton from parts that are safe by
construction (method, sanitised URL, numeric status, the standard reason
phrase, parameter and header names, allowlisted header values, exception
classes). Provider-supplied free text (response bodies, reason phrases,
error messages) is never part of a skeleton. A handler returns None when a
record does not have the expected shape, and the record is then treated as
unrecognised.

:data:`FORMS` lists the forms by message, per logger family; the Spotify
SDK's and the HTTP transports' pre-formatted lines are recognised by
:func:`known_form` from their text.
"""

import re
from collections.abc import Callable, Mapping

from podex.redaction import REDACTED
from podex.sdk_log_rules import (
    MAX_TEXT,
    describe_exception,
    header_value,
    is_credential_key,
    is_oversized,
    is_safe_header,
    masked_query,
    message_object_skeleton,
    reason_phrase,
    safe_headers,
    safe_url,
    sanitise_text,
    withheld,
)

Arguments = tuple[object, ...]
Handler = Callable[[Arguments], Arguments | None]

BODY_WITHHELD = "[withheld]"
PROVIDER_MESSAGE_WITHHELD = "[provider message withheld]"

_SHORT = 256
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
_TRACE_RE = re.compile(
    r"(?P<step>[a-z0-9_]{1,64}(?:\.[a-z0-9_]{1,64}){0,2})"
    r"\.(?P<phase>started|complete|failed)(?: |$)",
)
_TRACE_EXCEPTION_RE = re.compile(r"exception=(?P<cls>[A-Za-z_][A-Za-z0-9_.]{0,128})\(")
_TRACE_HOST_RE = re.compile(
    r"host='(?P<host>[A-Za-z0-9.\-:\[\]]{1,253})' port=(?P<port>\d{1,5})",
)
_TRACE_STATUS_RE = re.compile(
    r"return_value=\(b'(?P<protocol>HTTP/[0-9.]{1,5})', (?P<status>\d{3}), b'",
)
_TRACE_HEADER_RE = re.compile(
    r"""\(b'([^'\\]{1,128})', b'((?:[^'\\]|\\.){0,4096})'\)"""
)
_SCHEMES = frozenset({"http", "https"})


def _family(name: str, family: str) -> bool:
    """Return whether logger ``name`` is ``family`` or one of its children."""
    return name == family or name.startswith(f"{family}.")


def _short(value: object) -> str:
    """Return a short identifier (method, host, version) after the text rules."""
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    shown: str = (
        withheld(value)
        if not isinstance(value, str) or len(value) > _SHORT
        else sanitise_text(value)
    )
    return shown


def _number(value: object) -> bool:
    """Return whether ``value`` is a plain number."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def _no_arguments(arguments: Arguments) -> Arguments | None:
    """Accept a constant message."""
    return () if not arguments else None


def _numbers(arguments: Arguments) -> Arguments | None:
    """Accept a message whose arguments are all numbers."""
    return arguments if arguments and all(map(_number, arguments)) else None


def _one(handler: Callable[[object], object]) -> Handler:
    """Return a handler for a one-argument message."""

    def handle(arguments: Arguments) -> Arguments | None:
        return (handler(arguments[0]),) if len(arguments) == 1 else None

    return handle


def _urls(arguments: Arguments) -> Arguments | None:
    """Accept a message whose arguments are all URLs."""
    if not arguments:
        return None
    return tuple(safe_url(str(argument)) for argument in arguments)


def _method_and_url(arguments: Arguments) -> Arguments | None:
    """A method and a URL."""
    if len(arguments) != 2:
        return None
    return (_short(arguments[0]), safe_url(str(arguments[1])))


def _parameter_names(params: object) -> dict[object, str]:
    """Return a parameter mapping's names, with every value withheld."""
    if not isinstance(params, Mapping) or len(params) > 50:
        return {}
    return {
        _short(name): REDACTED if is_credential_key(name) else BODY_WITHHELD
        for name in params
    }


def _request_options(options: object) -> object:
    """Skeleton of the LLM SDK's request options.

    Method, sanitised URL, parameter names and allowlisted headers; every
    other option (the request body among them) is withheld by type and size.
    """
    if not isinstance(options, Mapping) or len(options) > 50:
        return withheld(options)
    skeleton: dict[object, object] = {}
    for key, value in options.items():
        if key == "method":
            skeleton[key] = _short(value)
        elif key == "url":
            skeleton[key] = safe_url(str(value))
        elif key == "params":
            skeleton[key] = _parameter_names(value)
        elif key == "headers":
            skeleton[key] = safe_headers(value)
        elif value is not None:
            shown = REDACTED if is_credential_key(key) else withheld(value)
            skeleton[_short(key)] = shown
    return skeleton


def _llm_response(arguments: Arguments) -> Arguments | None:
    """The LLM SDK's response line: reason replaced, headers allowlisted."""
    if len(arguments) != 5 or not isinstance(arguments[2], int):
        return None
    method, url, status, _reason, headers = arguments
    return (
        _short(method),
        safe_url(str(url)),
        status,
        reason_phrase(status),
        safe_headers(headers),
    )


def _request_line(arguments: Arguments) -> Arguments | None:
    """The HTTP clients' request line: the standard reason phrase."""
    if len(arguments) != 5 or not isinstance(arguments[3], int):
        return None
    method, url, version, status, _reason = arguments
    return (
        _short(method),
        safe_url(str(url)),
        _short(version),
        status,
        reason_phrase(status),
    )


def _connection_start(arguments: Arguments) -> Arguments | None:
    """The connection pool's new-connection line: count, host and port."""
    if len(arguments) != 3 or not isinstance(arguments[0], int):
        return None
    return (arguments[0], _short(arguments[1]), _short(arguments[2]))


def _pool_request(arguments: Arguments) -> Arguments | None:
    """The connection pool's request line: target without credentials."""
    if len(arguments) != 8 or str(arguments[0]) not in _SCHEMES:
        return None
    scheme, host, port, method, target, version, status, length = arguments
    return (
        scheme,
        _short(host),
        _short(port),
        _short(method),
        safe_url(str(target)),
        _short(version),
        status if _number(status) else withheld(status),
        length if _number(length) else _short(length),
    )


def _access_line(arguments: Arguments) -> Arguments | None:
    """The ASGI server's access line, kept as the five-item tuple it unpacks."""
    if len(arguments) != 5 or not isinstance(arguments[4], int):
        return None
    client, method, target, version, status = arguments
    return (
        _short(client),
        _short(method),
        safe_url(str(target)),
        _short(version),
        status,
    )


def _type_and_class(arguments: Arguments) -> Arguments | None:
    """A type and an exception: names only."""
    if len(arguments) != 2 or not isinstance(arguments[1], BaseException):
        return None
    kind = getattr(arguments[0], "__name__", None)
    shown = _short(kind) if isinstance(kind, str) else "?"
    return (shown, describe_exception(arguments[1]))


def _retrying(arguments: Arguments) -> Arguments | None:
    """The LLM SDK's retry notice: relative URL and delay."""
    if len(arguments) != 2 or not _number(arguments[1]):
        return None
    return (safe_url(str(arguments[0])), arguments[1])


def _headers_value(value: object) -> object:
    """A logged header container, or a withheld rendered one."""
    if isinstance(value, str | bytes):
        return withheld(value)
    return safe_headers(value)


def _request_object(value: object) -> object:
    """A prepared request printed by its structure, or withheld."""
    skeleton = message_object_skeleton(value)
    return skeleton if skeleton is not None else withheld(value)


def _redacted(_value: object) -> str:
    """A credential: always masked."""
    masked: str = REDACTED
    return masked


def _workspace(value: object) -> object:
    """The LLM SDK's workspace header, under the header allowlist."""
    return header_value("anthropic-workspace-id", value)


def _request_id(value: object) -> object:
    """The LLM SDK's request id header, under the header allowlist."""
    return header_value("request-id", value)


def safe_signing_text(text: object) -> str:
    """Apply the header allowlist and query rules to a signing dump.

    Args:
        text: A canonical request or string to sign.

    Returns:
        str: Each ``name:value`` line with the value kept only for allowlisted
            headers; each query line without credential values. A dump that
            is not text, or is too long to inspect, is withheld.
    """
    if not isinstance(text, str) or is_oversized(text):
        hidden: str = withheld(text)
        return hidden
    lines: list[str] = []
    for line in text.split("\n"):
        header = _HEADER_LINE_RE.fullmatch(line)
        if header is not None:
            shown = header[2] if is_safe_header(header[1]) else REDACTED
            lines.append(f"{header[1]}:{shown}")
        elif "=" in line and " " not in line:
            lines.append(masked_query(line))
        else:
            lines.append(line)
    cleaned: str = sanitise_text("\n".join(lines))
    return cleaned


_ANTHROPIC = ("anthropic",)
_HTTP_CLIENTS = ("httpx", "httpx2")
_URLLIB3 = ("urllib3",)
_BOTOCORE = ("botocore",)

FORMS: dict[str, tuple[tuple[str, ...], Handler]] = {
    # LLM SDK (anthropic._base_client, anthropic._response)
    "Request options: %s": (_ANTHROPIC, _one(_request_options)),
    "Sending HTTP Request: %s %s": (_ANTHROPIC, _method_and_url),
    'HTTP Response: %s %s "%i %s" %s': (_ANTHROPIC, _llm_response),
    "request_id: %s": (_ANTHROPIC, _one(_request_id)),
    "workspace_id: %s": (_ANTHROPIC, _one(_workspace)),
    "Retrying due to status code %i": (_ANTHROPIC, _numbers),
    "%i retries left": (_ANTHROPIC, _numbers),
    "1 retry left": (_ANTHROPIC, _no_arguments),
    "Not retrying": (_ANTHROPIC, _no_arguments),
    "Retrying as header `x-should-retry` is set to `true`": (_ANTHROPIC, _no_arguments),
    "Not retrying as header `x-should-retry` is set to `false`": (
        _ANTHROPIC,
        _no_arguments,
    ),
    "Encountered Exception": (_ANTHROPIC, _no_arguments),
    "Encountered httpx2.TimeoutException": (_ANTHROPIC, _no_arguments),
    "Could not read JSON from response data due to %s - %s": (
        _ANTHROPIC,
        _type_and_class,
    ),
    "Retrying request to %s in %f seconds": (_ANTHROPIC, _retrying),
    # HTTP clients
    'HTTP Request: %s %s "%s %d %s"': (_HTTP_CLIENTS, _request_line),
    # Connection pool (urllib3)
    "Starting new HTTP connection (%d): %s:%s": (_URLLIB3, _connection_start),
    "Starting new HTTPS connection (%d): %s:%s": (_URLLIB3, _connection_start),
    '%s://%s:%s "%s %s %s" %s %s': (_URLLIB3, _pool_request),
    "Resetting dropped connection: %s": (_URLLIB3, _one(_short)),
    "Retry: %s": (_URLLIB3, _urls),
    "Redirecting %s -> %s": (_URLLIB3, _urls),
    # Object storage client (botocore)
    "Sending http request: %s": (_BOTOCORE, _one(_request_object)),
    "Response headers: %r": (_BOTOCORE, _one(_headers_value)),
    "Response body:\n%r": (_BOTOCORE, _one(withheld)),
    "CanonicalRequest:\n%s": (_BOTOCORE, _one(safe_signing_text)),
    "StringToSign:\n%s": (_BOTOCORE, _one(safe_signing_text)),
    "String to sign: %s": (_BOTOCORE, _one(safe_signing_text)),
    "Signature:\n%s": (_BOTOCORE, _one(_redacted)),
    "Calculating signature using v4 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using v2 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using hmacv1 auth.": (_BOTOCORE, _no_arguments),
    "HTTP request method: %s": (_BOTOCORE, _one(_short)),
    "Exception received when sending HTTP request.": (_BOTOCORE, _no_arguments),
    "Not retrying request.": (_BOTOCORE, _no_arguments),
    "Retry needed, retrying request after delay of: %s": (_BOTOCORE, _numbers),
    "Max attempts of %s reached.": (_BOTOCORE, _numbers),
    'Failed to parse the "Expires" member as a timestamp: %s. The unparsed value '
    'is available in the response under "ExpiresString".': (
        _BOTOCORE,
        _one(withheld),
    ),
    "Received a tagged union response with member unknown to client: %s. "
    "Please upgrade SDK for full response support.": (_BOTOCORE, _one(withheld)),
    # ASGI server access log
    '%s - "%s %s HTTP/%s" %d': (("uvicorn.access",), _access_line),
}
"""Known record forms by message: the logger families and the handler."""


def _parameter_text(params: str) -> str:
    """Render a logged parameter mapping as its names, values withheld."""
    names = _QUOTED_KEY_RE.findall(params[:_BOUND])[:50]
    return repr(
        {
            name: REDACTED if is_credential_key(name) else BODY_WITHHELD
            for name in names
        },
    )


def _rendered_headers(rendered: str) -> object:
    """Parse a rendered header mapping and apply the header allowlist."""
    return safe_headers(_HEADER_ENTRY_RE.findall(rendered[:_BOUND]))


def _spotify_line(message: str) -> str | None:
    """Return the skeleton of a known Spotify SDK line, or None."""
    if message.startswith(_SPOTIFY_RESULTS):
        size = len(message) - len(_SPOTIFY_RESULTS)
        return f"{_SPOTIFY_RESULTS}[withheld response body ({size} characters)]"
    error = _SPOTIFY_ERROR_RE.match(message)
    if error is not None:
        return (
            f"HTTP Error for {error['method']} to {safe_url(error['url'])} with "
            f"Params: {_parameter_text(error['params'])} returned "
            f"{error['status']} due to {PROVIDER_MESSAGE_WITHHELD}"
        )
    request = _SPOTIFY_REQUEST_RE.match(message)
    if request is not None:
        return (
            f"Sending {request['method']} to {safe_url(request['url'])} with Params: "
            f"{_parameter_text(request['params'])} Headers: "
            f"{_rendered_headers(request['headers'])} and Body: {BODY_WITHHELD}"
        )
    token = _SPOTIFY_TOKEN_RE.match(message)
    if token is not None:
        return (
            f"Sending POST request to {safe_url(token['url'])} with Headers: "
            f"{_rendered_headers(token['headers'])} and Body: {BODY_WITHHELD}"
        )
    return None


def _trace_line(message: str) -> str | None:
    """Return the skeleton of an HTTP transport trace line, or None.

    The step and outcome are kept; a failed step keeps its exception class,
    a new connection its host and port, and a received response its
    protocol, numeric status and allowlisted headers. The provider's reason
    phrase and everything else are dropped.
    """
    trace = _TRACE_RE.match(message)
    if trace is None:
        return None
    head = f"{trace['step']}.{trace['phase']}"
    rest = message[trace.end() : trace.end() + MAX_TEXT]
    if trace["phase"] == "failed":
        failure = _TRACE_EXCEPTION_RE.search(rest)
        return f"{head} exception={failure['cls'] if failure else '?'}"
    if trace["step"] == "connect_tcp" and trace["phase"] == "started":
        address = _TRACE_HOST_RE.search(rest)
        if address is not None:
            return f"{head} host='{address['host']}' port={address['port']}"
    if trace["step"] == "receive_response_headers" and trace["phase"] == "complete":
        status = _TRACE_STATUS_RE.search(rest)
        if status is not None:
            headers = safe_headers(_TRACE_HEADER_RE.findall(rest)[:50])
            return (
                f"{head} protocol={status['protocol']} status={status['status']} "
                f"headers={headers}"
            )
    return head


def known_form(
    name: str,
    message: str,
    arguments: object,
) -> tuple[str, Arguments] | None:
    """Return a known record form's skeleton message and arguments.

    Args:
        name: The logger name.
        message: The record's message (its format string).
        arguments: The record's arguments.

    Returns:
        tuple | None: ``(message, arguments)`` for a known form with the
            expected shape; None for any other record.
    """
    positional = arguments if isinstance(arguments, tuple) else ()
    if isinstance(arguments, Mapping) and arguments:
        positional = (arguments,)
    entry = FORMS.get(message)
    if entry is not None:
        families, handler = entry
        if not any(_family(name, family) for family in families):
            return None
        cleaned = handler(positional)
        return None if cleaned is None else (message, cleaned)
    if positional:
        return None
    if _family(name, "spotipy"):
        skeleton = _spotify_line(message)
    elif _family(name, "httpcore") or _family(name, "httpcore2"):
        skeleton = _trace_line(message)
    else:
        skeleton = None
    return None if skeleton is None else (skeleton, ())
