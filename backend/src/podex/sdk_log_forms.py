"""Known third-party record forms and the skeleton each prints.

Third-party records are allowlisted by form at every level. A form is
identified by its exact format string (:data:`FORMS`) for records that pass
arguments, or by an anchored prefix for the few lines a library formats
itself (:func:`known_form`). Each form has a fixed number of arguments,
checked before any work is done. Its handler builds the record's arguments,
or its whole message, from validated parts only (:mod:`podex.sdk_log_rules`):
literals from this module, builtin numbers, HTTP methods from a fixed set,
sanitised URLs, allowlisted header names with validated values, validated
class names and standard reason phrases. Nothing else from the record is
copied. A part that does not validate is printed as ``[withheld]``; a record
whose shape or parse fails anywhere is withheld whole.

:data:`SERVER_FORMS` lists the request-material forms the ASGI server writes
on ``uvicorn.error`` (WebSocket handshakes and the WebSocket library's
request, header and frame lines); its other records are not changed here.
"""

import re
from collections.abc import Callable, Mapping
from itertools import islice

import urllib3

from podex.redaction import REDACTED
from podex.sdk_log_rules import (
    WITHHELD,
    Rendered,
    class_name,
    exact_bytes,
    exact_dict,
    exact_int,
    exact_str,
    exact_tuple,
    headers_skeleton,
    identifier,
    is_credential_key,
    method,
    number,
    qualified_name,
    reason_phrase,
    safe_header_name,
    status,
    url_skeleton,
)

Arguments = tuple[object, ...]
Handler = Callable[[Arguments], Arguments]

PROVIDER_MESSAGE_WITHHELD = "[provider message withheld]"
_HIDDEN: str = WITHHELD

MAX_PREFORMATTED = 8_192
"""Longest pre-formatted line parsed; a longer one is withheld."""

_MAX_NAMES = 16
_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_HOST_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]")
_CLIENT_RE = re.compile(r"[0-9A-Fa-f.:\[\]]{1,64}")
_VERSION_RE = re.compile(r"(?:HTTP/)?\d(?:\.\d)?")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,128}")
_ROLE_RE = re.compile(r"[A-Za-z0-9+=,.@_\-]{1,64}")
_ENDPOINT_RE = re.compile(r"[a-z0-9.\-]{1,128}")
_PERIOD_RE = re.compile(r"mandatory|advisory")
_TIMEZONE_RE = re.compile(r"[A-Za-z0-9_/+\-]{1,64}")
_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,128}")
_ALGORITHM_RE = re.compile(r"[A-Z0-9\-]{1,32}")
_TIMESTAMP_RE = re.compile(r"\d{8}T\d{6}Z")
_SCOPE_RE = re.compile(r"\d{8}/[a-z0-9\-]{1,32}/[a-z0-9\-]{1,32}/aws4_request")
_HASH_RE = re.compile(r"[0-9a-f]{64}|UNSIGNED-PAYLOAD|STREAMING-[A-Z0-9\-]{1,64}")
_HEADER_LINE_RE = re.compile(r"([a-z0-9\-]{1,128}):([^\n]{0,1024})")
_PLACEHOLDER_RE = re.compile(r"%(?:\.\d+)?[sdifr]|%%")
_SPOTIFY_ERROR_RE = re.compile(
    r"HTTP Error for ([A-Z]{1,16}) to ([^ ]{1,2048}) with Params: ",
)
_SPOTIFY_STATUS_RE = re.compile(r" returned (\d{3}) due to ")
_SPOTIFY_REQUEST_RE = re.compile(
    r"Sending ([A-Z]{1,16}) to ([^ ]{1,2048}) with Params: "
)
_SPOTIFY_TOKEN_RE = re.compile(r"Sending POST request to ([^ ]{1,2048}) with Headers: ")
_SPOTIFY_RESULTS = "RESULTS: "
_SPOTIFY_RATE_LIMIT = (
    "Your application has reached a rate/request limit. Retry will occur after: "
)
_SENTRY_SIZE_EXCEEDED = "HTTP 413: Event dropped due to exceeded envelope size limit"
_TRACE_STEPS = frozenset(
    {
        "close",
        "connect_tcp",
        "connect_unix_socket",
        "receive_remote_settings",
        "receive_response_body",
        "receive_response_headers",
        "response_closed",
        "retry",
        "send_connection_init",
        "send_request_body",
        "send_request_headers",
        "setup_socks5_connection",
        "start_tls",
    },
)
"""Trace steps the installed HTTP transports emit."""
_TRACE_PHASES = frozenset({"started", "complete", "failed"})
_TRACE_EXCEPTION_RE = re.compile(r"exception=([A-Za-z_][A-Za-z0-9_.]{0,128})\(")
_TRACE_HOST_RE = re.compile(
    r"host='([A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])' port=(\d{1,5})(?: |$)",
)
_TRACE_STATUS_RE = re.compile(r"return_value=\(b'(HTTP/[0-9.]{1,5})', (\d{3}), ")
_TRACE_DETAIL = 320


def _family(name: str, family: str) -> bool:
    """Return whether logger ``name`` is ``family`` or one of its children."""
    return name == family or name.startswith(f"{family}.")


def _validated(value: object, pattern: re.Pattern[str]) -> str:
    """Return short builtin text matching ``pattern``, else withheld."""
    if (isinstance(value, bytes) and exact_bytes(value)) and len(value) <= 512:
        value = value.decode("latin-1")
    if isinstance(value, int) and exact_int(value):
        value = str(int(value))
    shown: str = identifier(value, pattern)
    return shown


def _host(value: object) -> str:
    """A host name or address."""
    return _validated(value, _HOST_RE)


def _port(value: object) -> str:
    """A port number."""
    checked = number(value)
    return (
        str(checked) if (isinstance(checked, int) and exact_int(checked)) else _HIDDEN
    )


def _shown_number(value: object) -> object:
    """A builtin number, or withheld."""
    checked = number(value)
    return _HIDDEN if checked is None else checked


def _no_arguments(_arguments: Arguments) -> Arguments:
    """A constant message."""
    return ()


def _numbers(arguments: Arguments) -> Arguments:
    """A message whose arguments are numbers; others are withheld."""
    return tuple(_shown_number(argument) for argument in arguments)


def _withheld_arguments(arguments: Arguments) -> Arguments:
    """A constant message whose every argument is withheld."""
    return tuple(_HIDDEN for _ in arguments)


def _each(*parts: Callable[[object], object]) -> Handler:
    """Return a handler applying one validator per argument."""

    def handle(arguments: Arguments) -> Arguments:
        return tuple(
            part(argument) for part, argument in zip(parts, arguments, strict=True)
        )

    return handle


def _exception_class(value: object) -> str:
    """An exception's class, or a class's name."""
    named: str = _HIDDEN
    if isinstance(value, BaseException):
        named = class_name(value)
    elif isinstance(value, type):
        named = qualified_name(value)
    return named


def _withheld_value(_value: object) -> str:
    """Any value: withheld."""
    return _HIDDEN


def _redacted(_value: object) -> str:
    """A credential: always masked."""
    masked: str = REDACTED
    return masked


def _status(value: object) -> object:
    """An HTTP status code."""
    checked = status(value)
    return _HIDDEN if checked is None else checked


def _status_line(arguments: Arguments) -> Arguments:
    """A response status line: the status and its standard phrase."""
    return (_access_status(arguments[0]), reason_phrase(arguments[0]))


def _names(mapping: object) -> str:
    """Print a mapping's validated names, values withheld or masked.

    Only the first names up to the display bound are read.
    """
    if not (isinstance(mapping, dict) and exact_dict(mapping)):
        return _HIDDEN
    names: dict[str, str] = {}
    for name in islice(mapping, _MAX_NAMES):
        if (isinstance(name, str) and exact_str(name)) and _NAME_RE.fullmatch(name):
            names[name] = REDACTED if is_credential_key(name) else _HIDDEN
    others = len(mapping) - len(names)
    return f"{names}" + (f" (+{others} other names)" if others > 0 else "")


def _request_options(options: object) -> object:
    """The LLM SDK's request options: method, URL, names, allowlisted headers."""
    if not (isinstance(options, dict) and exact_dict(options)):
        return _HIDDEN
    known = sum(1 for key in ("method", "url", "params", "headers") if key in options)
    return Rendered(
        f"method={method(options.get('method'))} "
        f"url={url_skeleton(options.get('url', ''))} "
        f"params={_names(options.get('params', {}))} "
        f"headers={headers_skeleton(options.get('headers', {}))} "
        f"(+{len(options) - known} other options)",
    )


def _response_headers(value: object) -> object:
    """A header container."""
    return Rendered(headers_skeleton(value))


def _request_object(value: object) -> object:
    """The storage client's prepared request: method, URL, allowlisted headers."""
    if qualified_name(type(value)) != "botocore.awsrequest.AWSPreparedRequest":
        return _HIDDEN
    return Rendered(
        "<botocore.awsrequest.AWSPreparedRequest "
        f"method={method(getattr(value, 'method', None))} "
        f"url={url_skeleton(getattr(value, 'url', None))} "
        f"headers={headers_skeleton(getattr(value, 'headers', None))}>",
    )


def _response_body(body: object) -> object:
    """A response body: its size only."""
    if isinstance(body, bytes | bytearray) and type(body) in (bytes, bytearray):
        return Rendered(f"[withheld response body ({len(body)} bytes)]")
    return Rendered("[withheld response body]")


def _canonical_request(text: object) -> object:
    """A canonical request: method, path, allowlisted headers, payload hash."""
    if not (isinstance(text, str) and exact_str(text)) or len(text) > MAX_PREFORMATTED:
        return _HIDDEN
    lines = text.split("\n")
    if len(lines) < 6:
        return _HIDDEN
    query = f"?{lines[2]}" if lines[2] else ""
    headers = [
        (match[1], match[2])
        for line in lines[3:-3]
        if (match := _HEADER_LINE_RE.fullmatch(line)) is not None
    ]
    payload = identifier(lines[-1], _HASH_RE)
    signed = len(lines[-2].split(";")) if lines[-2] else 0
    return Rendered(
        f"{method(lines[0])} {url_skeleton(lines[1] + query)} "
        f"headers={headers_skeleton(headers)} signed_headers={signed} "
        f"payload={payload}",
    )


def _string_to_sign(text: object) -> object:
    """A signature version 4 string to sign: each line validated."""
    if not (isinstance(text, str) and exact_str(text)) or len(text) > MAX_PREFORMATTED:
        return _HIDDEN
    lines = text.split("\n")
    if len(lines) != 4:
        return _HIDDEN
    patterns = (_ALGORITHM_RE, _TIMESTAMP_RE, _SCOPE_RE, _HASH_RE)
    shown = [
        identifier(line, pattern) for line, pattern in zip(lines, patterns, strict=True)
    ]
    return Rendered("\n".join(shown))


def _retry_total(value: object) -> object:
    """A retry policy as its remaining total."""
    if not isinstance(value, urllib3.util.Retry):
        return Rendered(f"Retry(total={_HIDDEN})")
    return Rendered(f"Retry(total={_shown_number(getattr(value, 'total', None))})")


def _client(value: object) -> str:
    """A client address, ``host:port``."""
    if not (isinstance(value, str) and exact_str(value)) or len(value) > 128:
        return _HIDDEN
    host, _, port = value.rpartition(":")
    return f"{_validated(host, _CLIENT_RE)}:{_port(port)}" if host else _HIDDEN


def _access_status(value: object) -> int:
    """The access line's status, an integer the formatter can print with %d."""
    checked = status(value)
    return 0 if checked is None else checked


def _header_line(arguments: Arguments) -> Arguments:
    """One header line: the name if allowlisted, the value always withheld."""
    name = safe_header_name(arguments[0])
    return (_HIDDEN if name is None else name, _HIDDEN)


def _llm_response(arguments: Arguments) -> Arguments:
    """The LLM SDK's response line: standard phrase, allowlisted headers."""
    request_method, url, code, _reason, headers = arguments
    return (
        method(request_method),
        url_skeleton(url),
        _status(code),
        reason_phrase(code),
        _response_headers(headers),
    )


def _request_line(arguments: Arguments) -> Arguments:
    """The HTTP clients' request line: standard phrase."""
    request_method, url, version, code, _reason = arguments
    return (
        method(request_method),
        url_skeleton(url),
        _version(version),
        _status(code),
        reason_phrase(code),
    )


def _version(value: object) -> str:
    """An HTTP version."""
    return _validated(value, _VERSION_RE)


def _request_id(value: object) -> str:
    """The LLM SDK's request id."""
    return _validated(value, _REQUEST_ID_RE)


def _role(value: object) -> str:
    """An IAM role name returned by the metadata service."""
    return _validated(value, _ROLE_RE)


def _period(value: object) -> str:
    """A credential refresh period."""
    return _validated(value, _PERIOD_RE)


def _endpoint(value: object) -> str:
    """A deprecated endpoint name."""
    return _validated(value, _ENDPOINT_RE)


def _timezone(value: object) -> str:
    """A database time zone name."""
    return _validated(value, _TIMEZONE_RE)


def _arity(message: str) -> int:
    """Return the number of arguments a format string takes."""
    return sum(1 for match in _PLACEHOLDER_RE.finditer(message) if match[0] != "%%")


_ANTHROPIC = ("anthropic",)
_HTTP_CLIENTS = ("httpx", "httpx2")
_URLLIB3 = ("urllib3",)
_SPOTIPY = ("spotipy",)
_BOTOCORE = ("botocore",)
_SENTRY = ("sentry_sdk",)
_PSYCOPG = ("psycopg",)
_OTEL = ("opentelemetry",)

_FORM_TABLE: dict[str, tuple[tuple[str, ...], Handler]] = {
    # LLM SDK
    "Request options: %s": (_ANTHROPIC, _each(_request_options)),
    "Sending HTTP Request: %s %s": (_ANTHROPIC, _each(method, url_skeleton)),
    'HTTP Response: %s %s "%i %s" %s': (_ANTHROPIC, _llm_response),
    "request_id: %s": (_ANTHROPIC, _each(_request_id)),
    "workspace_id: %s": (_ANTHROPIC, _withheld_arguments),
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
        _each(_exception_class, _exception_class),
    ),
    "Retrying request to %s in %f seconds": (
        _ANTHROPIC,
        _each(url_skeleton, _shown_number),
    ),
    "Could not load the OpenTelemetry propagators, so requests carry no trace "
    "context: %s": (_ANTHROPIC, _each(_exception_class)),
    # HTTP clients
    'HTTP Request: %s %s "%s %d %s"': (_HTTP_CLIENTS, _request_line),
    # Connection pool
    "Starting new HTTP connection (%d): %s:%s": (
        _URLLIB3,
        _each(_shown_number, _host, _port),
    ),
    "Starting new HTTPS connection (%d): %s:%s": (
        _URLLIB3,
        _each(_shown_number, _host, _port),
    ),
    '%s://%s:%s "%s %s %s" %s %s': (
        _URLLIB3,
        _each(
            lambda scheme: scheme if scheme in ("http", "https") else _HIDDEN,
            _host,
            _port,
            method,
            url_skeleton,
            _version,
            _shown_number,
            _shown_number,
        ),
    ),
    "Resetting dropped connection: %s": (_URLLIB3, _each(_host)),
    "Retry: %s": (_URLLIB3, _each(url_skeleton)),
    "Redirecting %s -> %s": (_URLLIB3, _each(url_skeleton, url_skeleton)),
    "Retrying (%r) after connection broken by '%r': %s": (
        _URLLIB3,
        _each(
            _retry_total, lambda error: Rendered(_exception_class(error)), url_skeleton
        ),
    ),
    "Connection pool is full, discarding connection: %s. Connection pool size: %s": (
        _URLLIB3,
        _each(_host, _shown_number),
    ),
    "Failed to parse headers (url=%s): %s": (
        _URLLIB3,
        _each(url_skeleton, _exception_class),
    ),
    "Certificate did not match expected hostname: %s. Certificate: %s": (
        _URLLIB3,
        _each(_host, _withheld_value),
    ),
    # Spotify SDK (constant warnings and errors)
    "Max Retries reached": (_SPOTIPY, _no_arguments),
    "Specify either context uri or uris, not both": (_SPOTIPY, _no_arguments),
    "URIs must be a list": (_SPOTIPY, _no_arguments),
    "Position_ms must be an integer": (_SPOTIPY, _no_arguments),
    "Invalid state": (_SPOTIPY, _no_arguments),
    "Volume must be an integer": (_SPOTIPY, _no_arguments),
    "Volume must be between 0 and 100, inclusive": (_SPOTIPY, _no_arguments),
    "state must be a boolean": (_SPOTIPY, _no_arguments),
    # Object storage client
    "Sending http request: %s": (_BOTOCORE, _each(_request_object)),
    "Response headers: %r": (_BOTOCORE, _each(_response_headers)),
    "Response body:\n%r": (_BOTOCORE, _each(_response_body)),
    "CanonicalRequest:\n%s": (_BOTOCORE, _each(_canonical_request)),
    "StringToSign:\n%s": (_BOTOCORE, _each(_string_to_sign)),
    "String to sign: %s": (_BOTOCORE, _withheld_arguments),
    "Signature:\n%s": (_BOTOCORE, _each(_redacted)),
    "Calculating signature using v4 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using v2 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using hmacv1 auth.": (_BOTOCORE, _no_arguments),
    "HTTP request method: %s": (_BOTOCORE, _each(method)),
    "Exception received when sending HTTP request.": (_BOTOCORE, _no_arguments),
    "Not retrying request.": (_BOTOCORE, _no_arguments),
    "Retry needed but retry quota reached, not retrying request.": (
        _BOTOCORE,
        _no_arguments,
    ),
    "Retry needed, retrying request after delay of: %s": (_BOTOCORE, _numbers),
    "Response received to retry, sleeping for %s seconds": (_BOTOCORE, _numbers),
    "Max attempts of %s reached.": (_BOTOCORE, _numbers),
    "Found credentials in environment variables.": (_BOTOCORE, _no_arguments),
    "Found credentials in AWS_CREDENTIAL_FILE.": (_BOTOCORE, _no_arguments),
    "Found credentials in shared credentials file: %s": (
        _BOTOCORE,
        _withheld_arguments,
    ),
    "Credentials found in config file: %s": (_BOTOCORE, _withheld_arguments),
    "Found credentials in boto config file: %s": (_BOTOCORE, _withheld_arguments),
    "Found credentials from IAM Role: %s": (_BOTOCORE, _each(_role)),
    "Refreshing temporary credentials failed during %s refresh period.": (
        _BOTOCORE,
        _each(_period),
    ),
    "Credentials were refreshed, but the refreshed credentials are still expired.": (
        _BOTOCORE,
        _no_arguments,
    ),
    "Attempting credential expiration extension due to a credential service "
    "availability issue. A refresh of these credentials will be attempted again "
    "within the next %.0f minutes.": (_BOTOCORE, _numbers),
    "Client is configured with the deprecated endpoint: %s": (
        _BOTOCORE,
        _each(_endpoint),
    ),
    'Failed to parse the "Expires" member as a timestamp: %s. The unparsed value '
    'is available in the response under "ExpiresString".': (
        _BOTOCORE,
        _withheld_arguments,
    ),
    "Received a tagged union response with member unknown to client: %s. "
    "Please upgrade SDK for full response support.": (_BOTOCORE, _withheld_arguments),
    # Error-reporting SDK
    "Unexpected status code: %s (body: %s)": (
        _SENTRY,
        _each(_status, _withheld_value),
    ),
    # Database driver
    "unknown PostgreSQL timezone: %r; will use UTC": (_PSYCOPG, _each(_timezone)),
    # Telemetry API
    "Overriding of current TracerProvider is not allowed": (_OTEL, _no_arguments),
    "Overriding of current MeterProvider is not allowed": (_OTEL, _no_arguments),
    "Overriding of current LoggerProvider is not allowed": (_OTEL, _no_arguments),
    # ASGI server access log
    '%s - "%s %s HTTP/%s" %d': (
        ("uvicorn.access",),
        _each(_client, method, url_skeleton, _version, _access_status),
    ),
}

FORMS: dict[str, tuple[tuple[str, ...], int, Handler]] = {
    message: (families, _arity(message), handler)
    for message, (families, handler) in _FORM_TABLE.items()
}
"""Known record forms by exact format string: families, arity and handler."""

_SERVER_TABLE: dict[str, Handler] = {
    '%s - "WebSocket %s" [accepted]': _each(_client, url_skeleton),
    '%s - "WebSocket %s" 403': _each(_client, url_skeleton),
    '%s - "WebSocket %s" %d': _each(_client, url_skeleton, _access_status),
    "< GET %s HTTP/1.1": _each(url_skeleton),
    "< %s %s %s": _each(method, url_skeleton, _version),
    "< %s: %s": _header_line,
    "> %s: %s": _header_line,
    "< %s": _withheld_arguments,
    "> %s": _withheld_arguments,
    "> HTTP/1.1 %d %s": _status_line,
}

SERVER_FORMS: dict[str, tuple[int, Handler]] = {
    message: (_arity(message), handler) for message, handler in _SERVER_TABLE.items()
}
"""Request-material forms on ``uvicorn.error``: arity and handler."""


def _spotify_rate_limit(message: str) -> str | None:
    """The rate-limit warning: the delay if it is a number."""
    if not (message.startswith(_SPOTIFY_RATE_LIMIT) and message.endswith(" s")):
        return None
    delay = number(message[len(_SPOTIFY_RATE_LIMIT) : -2])
    shown = _HIDDEN if delay is None else delay
    return f"{_SPOTIFY_RATE_LIMIT}{shown} s"


def _spotify_error(message: str) -> str | None:
    """The error line: method, URL and status; the provider's message withheld.

    Only the anchored prefix and the status after the first ``returned``
    are read; if either does not parse, the line is withheld.
    """
    error = _SPOTIFY_ERROR_RE.match(message)
    position = message.find(" returned ", error.end()) if error is not None else -1
    code = _SPOTIFY_STATUS_RE.match(message, position) if position >= 0 else None
    if error is None or code is None:
        return None
    return (
        f"HTTP Error for {method(error[1])} to {url_skeleton(error[2])} "
        f"returned {code[1]} due to {PROVIDER_MESSAGE_WITHHELD}"
    )


def _spotify_request(message: str) -> str | None:
    """A request line: method and URL only; nothing after them is read."""
    request = _SPOTIFY_REQUEST_RE.match(message)
    if request is not None:
        return f"Sending {method(request[1])} to {url_skeleton(request[2])}"
    token = _SPOTIFY_TOKEN_RE.match(message)
    if token is not None:
        return f"Sending POST request to {url_skeleton(token[1])}"
    return None


def _spotify_line(message: str) -> str | None:
    """Return the skeleton of a known Spotify SDK line, or None.

    ``RESULTS:`` prints the body's size whatever its length; any other line
    longer than :data:`MAX_PREFORMATTED` is withheld before it is matched.
    """
    if message.startswith(_SPOTIFY_RESULTS):
        size = len(message) - len(_SPOTIFY_RESULTS)
        return f"{_SPOTIFY_RESULTS}[withheld response body ({size} characters)]"
    if len(message) > MAX_PREFORMATTED:
        return None
    for parse in (_spotify_rate_limit, _spotify_error, _spotify_request):
        skeleton = parse(message)
        if skeleton is not None:
            return skeleton
    return None


def _trace_line(message: str) -> str | None:
    """Return the skeleton of an HTTP transport trace line, or None.

    The step must be one the installed transports emit. A failed step keeps
    its validated exception class, a new connection its host and port, a
    received response its protocol and status; nothing else is read.
    """
    head, _, rest = message[:_TRACE_DETAIL].partition(" ")
    step, _, phase = head.rpartition(".")
    if step not in _TRACE_STEPS or phase not in _TRACE_PHASES:
        return None
    if phase == "failed":
        failure = _TRACE_EXCEPTION_RE.match(rest)
        shown = identifier(failure[1], _CLASS_RE) if failure else _HIDDEN
        return f"{head} exception={shown}"
    if step == "connect_tcp" and phase == "started":
        address = _TRACE_HOST_RE.match(rest)
        if address is not None:
            return f"{head} host='{address[1]}' port={address[2]}"
    if step == "receive_response_headers" and phase == "complete":
        response = _TRACE_STATUS_RE.match(rest)
        if response is not None:
            return f"{head} protocol={response[1]} status={response[2]}"
    return head


def _sentry_line(message: str) -> str | None:
    """The error-reporting SDK's oversized-envelope line: body withheld."""
    if message.startswith(_SENTRY_SIZE_EXCEEDED):
        return f"{_SENTRY_SIZE_EXCEEDED} (body: {_HIDDEN})"
    return None


def _preformatted(name: str, message: str) -> str | None:
    """Return the skeleton of a known pre-formatted line, or None."""
    if _family(name, "spotipy"):
        return _spotify_line(message)
    if _family(name, "httpcore") or _family(name, "httpcore2"):
        return _trace_line(message)
    if _family(name, "sentry_sdk"):
        return _sentry_line(message)
    return None


def _positional(arguments: object) -> Arguments:
    """Return a record's arguments as a tuple (a lone mapping as one item)."""
    if isinstance(arguments, tuple) and exact_tuple(arguments):
        return arguments
    if isinstance(arguments, Mapping) and arguments:
        return (arguments,)
    return ()


def known_form(
    name: str,
    message: object,
    arguments: object,
) -> tuple[str, Arguments] | None:
    """Return a known record form's skeleton message and arguments.

    Args:
        name: The logger name.
        message: The record's message (its format string).
        arguments: The record's arguments.

    Returns:
        tuple | None: ``(message, arguments)`` built from validated parts for
            a known form with the expected arity; None for any other record.
    """
    if not (isinstance(message, str) and exact_str(message)):
        return None
    positional = _positional(arguments)
    entry = FORMS.get(message)
    if entry is not None:
        families, arity, handler = entry
        if len(positional) != arity or not any(
            _family(name, family) for family in families
        ):
            return None
        return (message, handler(positional))
    if positional:
        return None
    skeleton = _preformatted(name, message)
    return None if skeleton is None else (skeleton, ())


def server_form(message: object, arguments: object) -> tuple[str, Arguments] | None:
    """Return the skeleton of a request-material record on ``uvicorn.error``.

    Args:
        message: The record's message (its format string).
        arguments: The record's arguments.

    Returns:
        tuple | None: ``(message, arguments)`` for a server form with the
            expected arity; None for any other record.
    """
    if not (isinstance(message, str) and exact_str(message)):
        return None
    entry = SERVER_FORMS.get(message)
    if entry is None:
        return None
    arity, handler = entry
    positional = _positional(arguments)
    if len(positional) != arity:
        return (message, tuple(_HIDDEN for _ in range(arity)))
    return (message, handler(positional))


def is_server_form(message: object) -> bool:
    """Return whether ``message`` is one of :data:`SERVER_FORMS`.

    Args:
        message: A record's message.

    Returns:
        bool: True for a request-material form of the ASGI server.
    """
    return (isinstance(message, str) and exact_str(message)) and message in SERVER_FORMS
