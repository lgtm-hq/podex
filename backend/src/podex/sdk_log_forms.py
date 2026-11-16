"""Known third-party SDK record forms and the skeleton each prints.

Third-party records are allowlisted by form at every level. A form is
identified by its exact format string (:data:`FORMS`) for records that pass
arguments, or by a full-match pattern (:data:`PREFORMATTED`) for the few SDK
lines that arrive already formatted. Each handler builds the record's
arguments, or its whole message, from validated parts only
(:mod:`podex.sdk_log_rules`): literals from this module, numbers, HTTP
methods from a fixed set, sanitised URLs, allowlisted headers, validated
class names and standard reason phrases. Nothing else from the record is
copied. A part that does not validate is printed as ``[withheld]``; a
handler that cannot place the record returns None, and the record is then
withheld whole.
"""

import ast
import re
from collections.abc import Callable, Mapping

from podex.redaction import REDACTED
from podex.sdk_log_rules import (
    WITHHELD,
    Rendered,
    class_name,
    headers_skeleton,
    identifier,
    is_credential_key,
    method,
    number,
    qualified_name,
    reason_phrase,
    status,
    url_skeleton,
)

Arguments = tuple[object, ...]
Handler = Callable[[Arguments], Arguments | None]
Builder = Callable[[re.Match[str]], str]

PROVIDER_MESSAGE_WITHHELD = "[provider message withheld]"
_HIDDEN: str = WITHHELD

MAX_MESSAGE = 65_536
"""Longest message matched against a pre-formatted pattern."""

_MAX_LITERAL = 16_384
_MAX_NAMES = 16
_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_HOST_RE = re.compile(r"[A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\]")
_CLIENT_RE = re.compile(r"[0-9A-Fa-f.:\[\]]{1,64}")
_VERSION_RE = re.compile(r"(?:HTTP/)?\d(?:\.\d)?")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,128}")
_ROLE_RE = re.compile(r"[A-Za-z0-9+=,.@_\-]{1,64}")
_ENDPOINT_RE = re.compile(r"[a-z0-9.\-]{1,128}")
_PERIOD_RE = re.compile(r"mandatory|advisory")
_STATUS_TEXT_RE = re.compile(r"\d{3}")
_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,128}")
_ALGORITHM_RE = re.compile(r"[A-Z0-9\-]{1,32}")
_TIMESTAMP_RE = re.compile(r"\d{8}T\d{6}Z")
_SCOPE_RE = re.compile(r"\d{8}/[a-z0-9\-]{1,32}/[a-z0-9\-]{1,32}/aws4_request")
_HASH_RE = re.compile(r"[0-9a-f]{64}|UNSIGNED-PAYLOAD|STREAMING-[A-Z0-9\-]{1,64}")
_HEADER_LINE_RE = re.compile(r"([a-z0-9\-]{1,128}):(.*)")
_XML_CODE_RE = re.compile(rb"<Code>([A-Za-z0-9.]{1,64})</Code>")
_JSON_CODE_RE = re.compile(
    rb'"(?:__type|code|Code)"\s*:\s*"(?:[^"#]{0,128}#)?([A-Za-z0-9.]{1,64})"',
)
_CODE_RE = re.compile(r"[A-Za-z0-9.]{1,64}")
_TRACE_EXCEPTION_RE = re.compile(r"exception=([A-Za-z_][A-Za-z0-9_.]{0,128})\(")
_TRACE_HOST_RE = re.compile(
    r"host='([A-Za-z0-9.\-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])' port=(\d{1,5})(?: |$)",
)
_TRACE_STATUS_RE = re.compile(r"return_value=\(b'(HTTP/[0-9.]{1,5})', (\d{3}), ")


def _family(name: str, family: str) -> bool:
    """Return whether logger ``name`` is ``family`` or one of its children."""
    return name == family or name.startswith(f"{family}.")


def _validated(value: object, pattern: re.Pattern[str]) -> str:
    """Return short text matching ``pattern``, numbers as text, else withheld."""
    if isinstance(value, bytes):
        value = value.decode("latin-1")
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    shown: str = identifier(value, pattern)
    return shown


def _host(value: object) -> str:
    """A host name or address."""
    return _validated(value, _HOST_RE)


def _port(value: object) -> str:
    """A port number."""
    checked = number(value)
    return str(checked) if isinstance(checked, int) else _HIDDEN


def _shown_number(value: object) -> object:
    """A number, or withheld."""
    checked = number(value)
    return WITHHELD if checked is None else checked


def _no_arguments(arguments: Arguments) -> Arguments | None:
    """A constant message."""
    return () if not arguments else None


def _numbers(arguments: Arguments) -> Arguments | None:
    """A message whose arguments are numbers; others are withheld."""
    if not arguments:
        return None
    return tuple(_shown_number(argument) for argument in arguments)


def _withheld_arguments(arguments: Arguments) -> Arguments | None:
    """A constant message whose every argument is withheld."""
    return tuple(WITHHELD for _ in arguments) if arguments else None


def _one(handler: Callable[[object], object]) -> Handler:
    """Return a handler for a one-argument message."""

    def handle(arguments: Arguments) -> Arguments | None:
        return (handler(arguments[0]),) if len(arguments) == 1 else None

    return handle


def _two(
    first: Callable[[object], object], second: Callable[[object], object]
) -> Handler:
    """Return a handler for a two-argument message."""

    def handle(arguments: Arguments) -> Arguments | None:
        if len(arguments) != 2:
            return None
        return (first(arguments[0]), second(arguments[1]))

    return handle


def _urls(arguments: Arguments) -> Arguments | None:
    """A message whose arguments are URLs."""
    if not arguments:
        return None
    return tuple(url_skeleton(argument) for argument in arguments)


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


def _status_and_phrase(code: object) -> tuple[object, str]:
    """A status code and its standard phrase."""
    checked = status(code)
    return (WITHHELD if checked is None else checked), reason_phrase(checked)


def _names(mapping: object) -> str:
    """Print a mapping's validated names, values withheld or masked."""
    if not isinstance(mapping, Mapping):
        return _HIDDEN
    names: dict[str, str] = {}
    for name in list(mapping)[:_MAX_NAMES]:
        if isinstance(name, str) and _NAME_RE.fullmatch(name):
            names[name] = REDACTED if is_credential_key(name) else WITHHELD
    others = len(mapping) - len(names)
    return f"{names}" + (f" (+{others} other names)" if others > 0 else "")


def _request_options(options: object) -> object:
    """The LLM SDK's request options: method, URL, names, allowlisted headers."""
    if not isinstance(options, Mapping):
        return _HIDDEN
    others = sum(
        1 for key in options if key not in ("method", "url", "params", "headers")
    )
    return Rendered(
        f"method={method(options.get('method'))} "
        f"url={url_skeleton(options.get('url', ''))} "
        f"params={_names(options.get('params', {}))} "
        f"headers={headers_skeleton(options.get('headers', {}))} "
        f"(+{others} other options)",
    )


def _llm_response(arguments: Arguments) -> Arguments | None:
    """The LLM SDK's response line: standard phrase, allowlisted headers."""
    if len(arguments) != 5:
        return None
    request_method, url, code, _reason, headers = arguments
    shown_status, phrase = _status_and_phrase(code)
    return (
        method(request_method),
        url_skeleton(url),
        shown_status,
        phrase,
        Rendered(headers_skeleton(headers)),
    )


def _request_line(arguments: Arguments) -> Arguments | None:
    """The HTTP clients' request line: standard phrase."""
    if len(arguments) != 5:
        return None
    request_method, url, version, code, _reason = arguments
    shown_status, phrase = _status_and_phrase(code)
    return (
        method(request_method),
        url_skeleton(url),
        _validated(version, _VERSION_RE),
        shown_status,
        phrase,
    )


def _connection_start(arguments: Arguments) -> Arguments | None:
    """The connection pool's new-connection line: count, host and port."""
    if len(arguments) != 3:
        return None
    return (_shown_number(arguments[0]), _host(arguments[1]), _port(arguments[2]))


def _pool_request(arguments: Arguments) -> Arguments | None:
    """The connection pool's request line: target without credentials."""
    if len(arguments) != 8:
        return None
    scheme, host, port, request_method, target, version, code, length = arguments
    return (
        scheme if scheme in ("http", "https") else WITHHELD,
        _host(host),
        _port(port),
        method(request_method),
        url_skeleton(target),
        _validated(version, _VERSION_RE),
        _shown_number(code),
        _shown_number(length),
    )


def _broken_connection(arguments: Arguments) -> Arguments | None:
    """The connection pool's retry warning: retries left, class, URL."""
    if len(arguments) != 3:
        return None
    total = _shown_number(getattr(arguments[0], "total", None))
    return (
        Rendered(f"Retry(total={total})"),
        Rendered(_exception_class(arguments[1])),
        url_skeleton(arguments[2]),
    )


def _access_line(arguments: Arguments) -> Arguments | None:
    """The ASGI server's access line, kept as the five-item tuple it unpacks."""
    if len(arguments) != 5:
        return None
    client, request_method, target, version, code = arguments
    host, _, port = str(client).rpartition(":")
    shown_client = f"{_validated(host, _CLIENT_RE)}:{_port(port)}" if host else WITHHELD
    checked = status(code)
    return (
        shown_client,
        method(request_method),
        url_skeleton(target),
        _validated(version, _VERSION_RE),
        0 if checked is None else checked,
    )


def _retrying(arguments: Arguments) -> Arguments | None:
    """The LLM SDK's retry notice: relative URL and delay."""
    if len(arguments) != 2:
        return None
    delay = number(arguments[1])
    return (url_skeleton(arguments[0]), delay if isinstance(delay, float | int) else 0)


def _request_object(value: object) -> object:
    """A prepared request: method, sanitised URL, allowlisted headers."""
    if getattr(value, "headers", None) is None or getattr(value, "url", None) is None:
        return _HIDDEN
    return Rendered(
        f"<{qualified_name(type(value))} "
        f"method={method(getattr(value, 'method', ''))} "
        f"url={url_skeleton(getattr(value, 'url', ''))} "
        f"headers={headers_skeleton(getattr(value, 'headers', {}))}>",
    )


def _response_body(body: object) -> object:
    """A response body: size, and the provider's error code if it validates."""
    if not isinstance(body, bytes | bytearray):
        return Rendered(f"[withheld response body ({type(body).__name__})]")
    head = bytes(body[:4_096])
    found = _XML_CODE_RE.search(head) or _JSON_CODE_RE.search(head)
    code = found.group(1).decode("ascii") if found else ""
    shown = identifier(code, _CODE_RE) if code else ""
    suffix = f"; error code {shown}" if shown else ""
    return Rendered(f"[withheld response body ({len(body)} bytes){suffix}]")


def _canonical_request(text: object) -> object:
    """A canonical request: method, path, allowlisted headers, payload hash."""
    if not isinstance(text, str) or len(text) > _MAX_LITERAL:
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
    if not isinstance(text, str) or len(text) > _MAX_LITERAL:
        return _HIDDEN
    lines = text.split("\n")
    if len(lines) != 4:
        return _HIDDEN
    patterns = (_ALGORITHM_RE, _TIMESTAMP_RE, _SCOPE_RE, _HASH_RE)
    shown = [
        identifier(line, pattern) for line, pattern in zip(lines, patterns, strict=True)
    ]
    return Rendered("\n".join(shown))


def _redacted(_value: object) -> str:
    """A credential: always masked."""
    masked: str = REDACTED
    return masked


def _sentry_status(arguments: Arguments) -> Arguments | None:
    """The error-reporting SDK's unexpected-status line: status, body withheld."""
    if len(arguments) != 2:
        return None
    checked = status(arguments[0])
    return ((WITHHELD if checked is None else checked), WITHHELD)


def _method_and_url(arguments: Arguments) -> Arguments | None:
    """A method and a URL."""
    if len(arguments) != 2:
        return None
    return (method(arguments[0]), url_skeleton(arguments[1]))


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


def _headers(value: object) -> object:
    """A header container."""
    return Rendered(headers_skeleton(value))


_ANTHROPIC = ("anthropic",)
_HTTP_CLIENTS = ("httpx", "httpx2")
_URLLIB3 = ("urllib3",)
_SPOTIPY = ("spotipy",)
_BOTOCORE = ("botocore",)
_SENTRY = ("sentry_sdk",)

FORMS: dict[str, tuple[tuple[str, ...], Handler]] = {
    # LLM SDK
    "Request options: %s": (_ANTHROPIC, _one(_request_options)),
    "Sending HTTP Request: %s %s": (_ANTHROPIC, _method_and_url),
    'HTTP Response: %s %s "%i %s" %s': (_ANTHROPIC, _llm_response),
    "request_id: %s": (_ANTHROPIC, _one(_request_id)),
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
        _two(_exception_class, _exception_class),
    ),
    "Retrying request to %s in %f seconds": (_ANTHROPIC, _retrying),
    "Could not load the OpenTelemetry propagators, so requests carry no trace "
    "context: %s": (_ANTHROPIC, _one(_exception_class)),
    # HTTP clients
    'HTTP Request: %s %s "%s %d %s"': (_HTTP_CLIENTS, _request_line),
    # Connection pool
    "Starting new HTTP connection (%d): %s:%s": (_URLLIB3, _connection_start),
    "Starting new HTTPS connection (%d): %s:%s": (_URLLIB3, _connection_start),
    '%s://%s:%s "%s %s %s" %s %s': (_URLLIB3, _pool_request),
    "Resetting dropped connection: %s": (_URLLIB3, _one(_host)),
    "Retry: %s": (_URLLIB3, _urls),
    "Redirecting %s -> %s": (_URLLIB3, _urls),
    "Retrying (%r) after connection broken by '%r': %s": (_URLLIB3, _broken_connection),
    "Connection pool is full, discarding connection: %s. Connection pool size: %s": (
        _URLLIB3,
        _two(_host, _shown_number),
    ),
    "Failed to parse headers (url=%s): %s": (
        _URLLIB3,
        _two(url_skeleton, _exception_class),
    ),
    "Certificate did not match expected hostname: %s. Certificate: %s": (
        _URLLIB3,
        _two(_host, _withheld_value),
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
    "Sending http request: %s": (_BOTOCORE, _one(_request_object)),
    "Response headers: %r": (_BOTOCORE, _one(_headers)),
    "Response body:\n%r": (_BOTOCORE, _one(_response_body)),
    "CanonicalRequest:\n%s": (_BOTOCORE, _one(_canonical_request)),
    "StringToSign:\n%s": (_BOTOCORE, _one(_string_to_sign)),
    "String to sign: %s": (_BOTOCORE, _withheld_arguments),
    "Signature:\n%s": (_BOTOCORE, _one(_redacted)),
    "Calculating signature using v4 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using v2 auth.": (_BOTOCORE, _no_arguments),
    "Calculating signature using hmacv1 auth.": (_BOTOCORE, _no_arguments),
    "HTTP request method: %s": (_BOTOCORE, _one(method)),
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
    "Found credentials from IAM Role: %s": (_BOTOCORE, _one(_role)),
    "Refreshing temporary credentials failed during %s refresh period.": (
        _BOTOCORE,
        _one(_period),
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
        _one(_endpoint),
    ),
    'Failed to parse the "Expires" member as a timestamp: %s. The unparsed value '
    'is available in the response under "ExpiresString".': (
        _BOTOCORE,
        _withheld_arguments,
    ),
    "Received a tagged union response with member unknown to client: %s. "
    "Please upgrade SDK for full response support.": (_BOTOCORE, _withheld_arguments),
    # Error-reporting SDK
    "Unexpected status code: %s (body: %s)": (_SENTRY, _sentry_status),
    # ASGI server access log
    '%s - "%s %s HTTP/%s" %d': (("uvicorn.access",), _access_line),
}
"""Known record forms by exact format string: logger families and handler."""


def _literal_mapping(text: str) -> object:
    """Parse a rendered literal mapping, bounded; None if it is not one."""
    parsed: object = None
    if len(text) <= _MAX_LITERAL:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
            parsed = None
    return parsed if isinstance(parsed, Mapping) else None


def _spotify_results(found: re.Match[str]) -> str:
    """The Spotify SDK's response body line: size only."""
    return f"RESULTS: [withheld response body ({len(found['body'])} characters)]"


def _spotify_error(found: re.Match[str]) -> str:
    """The Spotify SDK's error line: method, URL, parameter names, status."""
    return (
        f"HTTP Error for {method(found['method'])} to {url_skeleton(found['url'])} "
        f"with Params: {_names(_literal_mapping(found['params']))} returned "
        f"{_validated(found['status'], _STATUS_TEXT_RE)} due to "
        f"{PROVIDER_MESSAGE_WITHHELD}"
    )


def _spotify_request(found: re.Match[str]) -> str:
    """The Spotify SDK's request line: names and allowlisted headers only."""
    headers = _literal_mapping(found["headers"])
    return (
        f"Sending {method(found['method'])} to {url_skeleton(found['url'])} with "
        f"Params: {_names(_literal_mapping(found['params']))} Headers: "
        f"{headers_skeleton(headers if headers is not None else {})} and Body: "
        f"{WITHHELD}"
    )


def _spotify_token(found: re.Match[str]) -> str:
    """The Spotify SDK's token request line: allowlisted headers only."""
    headers = _literal_mapping(found["headers"])
    return (
        f"Sending POST request to {url_skeleton(found['url'])} with Headers: "
        f"{headers_skeleton(headers if headers is not None else {})} and Body: "
        f"{WITHHELD}"
    )


def _spotify_rate_limit(found: re.Match[str]) -> str:
    """The Spotify SDK's rate-limit warning: the delay if it is a number."""
    delay = number(found["delay"])
    return (
        "Your application has reached a rate/request limit. Retry will occur "
        f"after: {WITHHELD if delay is None else delay} s"
    )


def _transport_trace(found: re.Match[str]) -> str:
    """A transport trace line: step and outcome, then validated details only.

    A failed step keeps its exception class, a new connection its host and
    port, a received response its protocol and numeric status. Rendered
    header dumps are never parsed.
    """
    head = f"{found['step']}.{found['phase']}"
    rest = found["rest"] or ""
    if found["phase"] == "failed":
        failure = _TRACE_EXCEPTION_RE.match(rest)
        shown = identifier(failure[1], _CLASS_RE) if failure else WITHHELD
        return f"{head} exception={shown}"
    if found["step"] == "connect_tcp" and found["phase"] == "started":
        address = _TRACE_HOST_RE.match(rest)
        if address is not None:
            return f"{head} host='{address[1]}' port={address[2]}"
    if found["step"] == "receive_response_headers" and found["phase"] == "complete":
        response = _TRACE_STATUS_RE.match(rest)
        if response is not None:
            return f"{head} protocol={response[1]} status={response[2]}"
    return head


def _sentry_size_exceeded(_found: re.Match[str]) -> str:
    """The error-reporting SDK's oversized-envelope line: body withheld."""
    return (
        "HTTP 413: Event dropped due to exceeded envelope size limit "
        f"(body: {WITHHELD})"
    )


PREFORMATTED: tuple[tuple[str, re.Pattern[str], Builder], ...] = (
    ("spotipy", re.compile(r"RESULTS: (?P<body>.*)", re.DOTALL), _spotify_results),
    (
        "spotipy",
        re.compile(
            r"HTTP Error for (?P<method>[A-Z]{1,16}) to (?P<url>\S{1,4096}) with "
            r"Params: (?P<params>.*) returned (?P<status>\d{3}) due to .*",
            re.DOTALL,
        ),
        _spotify_error,
    ),
    (
        "spotipy",
        re.compile(
            r"Sending (?P<method>[A-Z]{1,16}) to (?P<url>\S{1,4096}) with Params: "
            r"(?P<params>.*) Headers: (?P<headers>\{.*\}) and Body: .*",
            re.DOTALL,
        ),
        _spotify_request,
    ),
    (
        "spotipy",
        re.compile(
            r"Sending POST request to (?P<url>\S{1,4096}) with Headers: "
            r"(?P<headers>\{.*\}) and Body: .*",
            re.DOTALL,
        ),
        _spotify_token,
    ),
    (
        "spotipy",
        re.compile(
            r"Your application has reached a rate/request limit\. Retry will occur "
            r"after: (?P<delay>.{0,64}) s",
            re.DOTALL,
        ),
        _spotify_rate_limit,
    ),
    (
        "httpcore",
        re.compile(
            r"(?P<step>[a-z0-9_]{1,64}(?:\.[a-z0-9_]{1,64}){0,2})"
            r"\.(?P<phase>started|complete|failed)(?: (?P<rest>.*))?",
            re.DOTALL,
        ),
        _transport_trace,
    ),
    (
        "sentry_sdk",
        re.compile(
            r"HTTP 413: Event dropped due to exceeded envelope size limit"
            r"(?: \(body: .*\))?",
            re.DOTALL,
        ),
        _sentry_size_exceeded,
    ),
)
"""Known pre-formatted lines: logger family root, full-match pattern, builder."""


def _preformatted(name: str, message: str) -> str | None:
    """Return the skeleton of a known pre-formatted line, or None."""
    if len(message) > MAX_MESSAGE:
        return None
    root = name.partition(".")[0]
    family = "httpcore" if root in ("httpcore", "httpcore2") else root
    for owner, pattern, builder in PREFORMATTED:
        if owner != family:
            continue
        found = pattern.fullmatch(message)
        if found is not None:
            return builder(found)
    return None


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
            a known form with the expected shape; None for any other record.
    """
    if not isinstance(message, str):
        return None
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
    skeleton = _preformatted(name, message)
    return None if skeleton is None else (skeleton, ())
