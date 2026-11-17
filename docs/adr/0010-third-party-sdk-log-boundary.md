<!-- markdownlint-disable MD041 -->

# 0010. Third-party log records are allowlisted by form

- Status: accepted
- Date: 2026-11-16
- Tags: security, observability, logging

Extends [0008](./0008-masked-secret-settings.md): it replaces 0008's two
per-logger filters (HTTP client request lines, Spotify SDK debug lines) and
narrows its non-choice of log-wide secret value scrubbing for third-party
records. 0008 is otherwise unchanged.

## Context

The application's log boundary (0008) withholds database error text from every
record. The libraries the application uses log on their own loggers, and
running each real client over a fake transport showed request options with the
prompt, request and response headers, response bodies, provider reason phrases
and error messages, rate-limit headers, credential-provider responses, signing
input with session tokens, WebSocket request lines and headers, and tracebacks
printing both messages and source lines.

Two earlier designs cleaned these records instead of allowlisting them: first
every record by structure and text rules, then DEBUG records by form with text
cleaning kept for INFO and above. Each review found further leaks: DEBUG forms
nobody had listed, provider text in INFO and ERROR lines that text rules cannot
recognise, header names that carry values, skeletons that copied provider parts,
and parsers that could be steered by the text they parsed. Cleaning unknown text
does not converge.

## Decision

1. **Third-party records are allowlisted by form at every level.** A record
   from a covered logger family is printed with content only if it matches a
   known form. Any other record keeps its logger name and level, and its
   message becomes `[record withheld: unrecognised form, N characters]`,
   followed by the validated class names of an attached exception; its
   arguments, traceback text and stack text are dropped. There is no text
   cleaning for third-party records. Application records are not changed.
2. A known form is identified by its exact format string and arity, checked
   before any work, for records that pass arguments; or by an anchored prefix
   for the few lines a library formats itself. A pre-formatted line is
   length-checked before any pattern runs (8,192 characters), patterns have no
   nested or overlapping quantifiers, and a line whose parse fails anywhere is
   withheld whole.
3. **Skeletons are built from validated parts, never copied from the record**:
   literals of the boundary's own code; builtin `int` and `float` values (an
   exact type, not a subclass) or short ASCII numeric text, emitted as
   builtins; HTTP methods from a fixed set; URLs given as builtin `str` (at
   most 2,048 characters) or as the HTTP clients' URL objects, printed from
   scheme, host, port and path (userinfo and fragment dropped; query names kept
   if they match a conservative pattern, credential-named values masked, other
   values kept only if short and conservative); header names from the
   allowlist only, never a credential-named one even under an allowlisted
   prefix, each value an exact builtin validated against a conservative pattern
   and length, every other header counted as `+N other headers`; exception
   class names validated as identifiers; the standard reason phrase for a
   status code. A part that does not validate is printed as `[withheld]`. The
   configured secret values of the loaded settings are replaced in every
   finished skeleton as a last step.
4. Every record is bounded: at most 16 headers and 16 query fields printed, 128
   characters per header value, 64 per query value, 1,000 header entries and 16
   mapping names read (lazily), a skeleton message of at most 8,192 characters
   and **at most 16,384 characters printed per record in total**, shared by the
   message, the rebuilt traceback and the stack text. Tracebacks print at most
   32 frames per exception, 16 exception group members and 8 chained links,
   each beyond its bound counted as `+N more`.
5. Known forms (`backend/src/podex/sdk_log_forms.py`) and what each prints:
   - **LLM SDK** (`anthropic`): `Request options` (method, URL, parameter
     names, allowlisted headers, count of other options); `Sending HTTP
     Request` (method, URL); `HTTP Response` (method, URL, status, standard
     phrase, allowlisted headers); `request_id` (validated id); `workspace_id`
     (withheld); retry notices with numbers; `Encountered Exception` and
     `Encountered httpx2.TimeoutException` (traceback rebuilt); `Could not read
     JSON ...` (class names); `Retrying request to ... in ... seconds` (INFO:
     URL, delay); `Could not load the OpenTelemetry propagators ...` (WARNING:
     class name).
   - **HTTP clients** (`httpx`, `httpx2`): `HTTP Request` (INFO: method, URL,
     version, status, standard phrase).
   - **HTTP transports** (`httpcore`, `httpcore2`) trace lines: only the
     thirteen steps the installed transports emit (`close`, `connect_tcp`,
     `connect_unix_socket`, `receive_remote_settings`, `receive_response_body`,
     `receive_response_headers`, `response_closed`, `retry`,
     `send_connection_init`, `send_request_body`, `send_request_headers`,
     `setup_socks5_connection`, `start_tls`) with `started`, `complete` or
     `failed`; a failed step its validated exception class;
     `connect_tcp.started` its host and port; `receive_response_headers.complete`
     its protocol and status only.
   - **Connection pool** (`urllib3`): new-connection lines (count, host, port);
     the request line (scheme, host, port, method, target, version, status,
     length); dropped-connection, retry and redirect notices (host, URLs); the
     WARNING lines `Retrying ... after connection broken by ...` (retries left,
     class, URL), `Connection pool is full ...` (host, size), `Failed to parse
     headers ...` (URL, class) and `Certificate did not match expected hostname
     ...` (host; certificate withheld).
   - **Spotify SDK** (`spotipy`): `RESULTS` (size only); the ERROR line `HTTP
     Error for ...` (method, URL, status; parameters and the provider message
     withheld); request and token lines (method and URL only; nothing after
     them is read); the rate-limit WARNING (numeric delay, else withheld);
     constant errors and parameter warnings.
   - **Object storage client** (`botocore`): the prepared request (only the
     library's own `AWSPreparedRequest`: method, URL, allowlisted headers);
     response headers (allowlisted); response body (size only); canonical
     request (method, path, allowlisted headers, count of signed headers,
     payload hash); string to sign (each line validated); signature (masked);
     signing, retry and attempt notices (constants and numbers); credential
     notices at INFO and WARNING (constant text, IAM role name if valid, file
     paths withheld; refresh failure with its period and a rebuilt traceback);
     the deprecated-endpoint warning (validated endpoint); the two warnings that
     quote response values (value withheld). The numeric status of each storage
     request is printed by the connection pool's request line.
   - **Error-reporting SDK** (`sentry_sdk`): `Unexpected status code` (status;
     body withheld) and the `HTTP 413` line (body withheld).
   - **Database driver** (`psycopg`): `unknown PostgreSQL timezone` (validated
     zone name).
   - **Telemetry API** (`opentelemetry`): the three provider-override warnings
     (constants).
   - **ASGI server access log** (`uvicorn.access`): the five-argument tuple its
     formatter unpacks (client, method, target, version, status).
6. **ASGI server log** (`uvicorn.error`): this logger carries request material
   for WebSocket upgrades, so its request-material forms are rewritten: the
   handshake lines `'%s - "WebSocket %s" [accepted]'`, `... 403` and `... %d`
   (client address, sanitised target, status); and the WebSocket library's
   lines on the same logger, `< %s %s %s` and `< GET %s HTTP/1.1` (method,
   sanitised target, version), `< %s: %s` and `> %s: %s` (the header name if
   allowlisted, the value always withheld), `< %s` and `> %s` (frames,
   withheld) and `> HTTP/1.1 %d %s` (status and standard phrase). Every other
   record of this logger (start-up lines, application exceptions) is left to
   0008's database-error boundary.
7. Covered families, by the logger names the installed versions use:
   `anthropic`, `httpx`, `httpx2`, `httpcore`, `httpcore2`, `urllib3`,
   `requests`, `charset_normalizer`, `spotipy`, `boto3`, `botocore`,
   `s3transfer`, `sentry_sdk`, `pydantic_settings`, `bs4`, `psycopg` (including
   `psycopg.debug`, `psycopg.pq` and `psycopg.pool`), `redis` (module loggers)
   and `push_response` (the redis parsers' push-message logger),
   `opentelemetry`, `uvicorn.access` and `uvicorn.asgi` (the server's message
   logger, active only at its trace level); plus the request-material forms of
   `uvicorn.error` above.
8. Tracebacks of known forms are rebuilt from the exception tree without
   source text or messages (file, line, function, validated class, with causes,
   contexts and exception group members), within the bounds of point 4; the
   configured secret values are then replaced. Cached traceback text that
   cannot be rebuilt is withheld, and stack text keeps its frame lines only.
   Because third-party records never print exception messages, the
   database-error pass is not run on them.
9. The boundary never raises into a logging call. On any internal failure, for
   any record, every content attribute is first set to a constant; then a
   placeholder naming the message type and exception classes is attempted; if
   that fails too, the constant `<unprintable: sanitiser failed>` remains.
10. Request lines stay at INFO. The default log level, the log format and what
    the application's own code logs are unchanged.

Not covered, and why:

- `sqlalchemy`: pinned at WARNING by the library; statement parameters are
  hidden by `hide_parameters`; database errors are withheld by 0008.
- `alembic`: migration progress lines from the application's own revisions.
- `fastapi`, `anyio`: framework warnings; no request or response material.
- `websockets`, `watchfiles`: when the ASGI server uses the WebSocket library,
  it passes `uvicorn.error` as the library's logger (covered above); the
  library's own `websockets.*` loggers are not used by the server; `watchfiles`
  serves the development reloader only.
- SMTP (`smtplib`): no logging; protocol traces only with `set_debuglevel`,
  which the application never sets.
- `feedparser`, `rapidfuzz`, `cryptography`, `email_validator`,
  `prometheus_client`, `pydantic`, `starlette`, `h11`: no logging calls.

## Consequences

- Operators see the request flow, statuses, request ids, rate-limit headers,
  retry and redirect notices and exception classes, at every level; every other
  third-party record shows its logger, level and size only. Diagnosing a
  library's own behaviour in depth needs a local run outside this boundary.
- A useful line from a covered library appears only after a known form is
  added for it, with a test; a library that logs on its own logger must be
  added to the families; a diagnostic header must be added to the allowlist.
- Credential-named keys such as `code`, `key` and `session` are masked
  wherever they appear as structured keys or query names; third-party free text
  is never printed, so they cannot over-mask it.
- Unrecognised records cost a dictionary lookup and a marker; known forms check
  arity and types before any work and are bounded by their output limits;
  application records cost one dictionary lookup on the logger name.

Known limits:

- URL paths are printed: a secret carried in a path segment (a reset token, an
  object storage key) is not masked unless it equals a configured secret value.
- Configured-secret replacement applies to the string parts of a skeleton; a
  configured secret that happens to equal a number or an exception class name
  is not replaced.
- Credentials read outside `Settings` (the LLM and Spotify SDKs read their own
  environment variables) are masked by name and structure only, not by value.
- A validated part can still be sensitive in context while matching the
  conservative patterns (a short query value, an IAM role name).
- Diagnostics that a settings library emits before any entrypoint installs the
  boundary (for example with its own debug switch enabled before import) are
  not covered here; that ordering is tracked in issue #835.
- Attributes added to a record after it is created (`extra=` fields, which
  `Logger.makeRecord` sets after the record factory runs) are not inspected.
- `asyncio`'s default exception handler (logged on the `asyncio` logger) and
  warnings captured through `logging.captureWarnings` (the `py.warnings`
  logger) are not covered.
- SQLAlchemy's DEBUG echo (`echo="debug"` or an explicit DEBUG level on its
  engine logger) prints result rows; it is an explicit opt-in and not covered.
- Records created without the record factory (`logging.makeLogRecord`) bypass
  the boundary; none of the covered libraries does this.
