<!-- markdownlint-disable MD041 -->

# 0010. Third-party log records are allowlisted by form

- Status: proposed
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
input with session tokens, and tracebacks printing both messages and source
lines.

Two earlier designs cleaned these records instead of allowlisting them: first
every record by structure and text rules, then DEBUG records by form with text
cleaning kept for INFO and above. Each review found further leaks: DEBUG forms
nobody had listed, provider text in INFO and ERROR lines that text rules cannot
recognise (a `Retry-After` value, a provider's error message after a long
query), header names that carry values, and skeletons that copied provider
parts. Cleaning unknown text does not converge.

## Decision

1. **Third-party records are allowlisted by form at every level.** A record
   from a covered logger family is printed with content only if it matches a
   known form. Any other record keeps its logger name and level, and its
   message becomes `[record withheld: unrecognised form, N characters]`,
   followed by the validated class names of an attached exception; its
   arguments, traceback text and stack text are dropped. There is no text
   cleaning for third-party records. Application records are not changed.
2. A known form is identified by its exact format string for records that pass
   arguments, or by a full-match pattern for the few lines an SDK formats
   itself. A line that looks like a known form but does not parse is withheld.
3. **Skeletons are built from validated parts, never copied from the record**:
   literals of the boundary's own code; numbers that are, or parse as, numbers;
   HTTP methods from a fixed set; URLs from scheme, host, port and path (userinfo
   and fragment dropped; query names kept if they match a conservative pattern,
   credential-named values masked, other values kept only if short and
   conservative); header names from the allowlist only, never a credential-named
   one even under an allowlisted prefix, each value validated against a
   conservative pattern and length, every other header counted as
   `+N other headers`; exception class names validated as identifiers; the
   standard reason phrase for a status code. A part that does not validate is
   printed as `[withheld]`. The configured secret values of the loaded settings
   are replaced in every finished skeleton as a last step.
4. Skeletons are bounded: at most 16 headers and 16 query fields printed, 128
   characters per header value, 64 per query value, 2,048 per URL, 1,000 header
   entries read, 65,536 characters of a pre-formatted line parsed, and 8,192
   characters printed in total (beyond it, the record is withheld).
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
   - **HTTP transports** (`httpcore`, `httpcore2`) trace lines: step and outcome;
     a failed step its validated exception class; `connect_tcp.started` its host
     and port; `receive_response_headers.complete` its protocol and status only.
     Rendered header dumps are never parsed.
   - **Connection pool** (`urllib3`): new-connection lines (count, host, port);
     the request line (scheme, host, port, method, target, version, status,
     length); dropped-connection, retry and redirect notices (host, URLs); the
     WARNING lines `Retrying ... after connection broken by ...` (retries left,
     class, URL), `Connection pool is full ...` (host, size), `Failed to parse
     headers ...` (URL, class) and `Certificate did not match expected hostname
     ...` (host; certificate withheld).
   - **Spotify SDK** (`spotipy`): `RESULTS` (size only); the ERROR line `HTTP
     Error for ...` (method, URL, parameter names, status; provider message
     withheld); request and token lines (parameter names, allowlisted headers;
     body withheld); the rate-limit WARNING (numeric delay, else withheld);
     constant errors and parameter warnings.
   - **Object storage client** (`botocore`): prepared request (method, URL,
     allowlisted headers); response headers (allowlisted); response body (size
     and the provider's error code if it is a short identifier, never the
     message); canonical request (method, path, allowlisted headers, count of
     signed headers, payload hash); string to sign (each line validated);
     signature (masked); signing, retry and attempt notices (constants and
     numbers); credential notices at INFO and WARNING (constant text, IAM role
     name if valid, file paths withheld; refresh failure with its period and a
     rebuilt traceback); the deprecated-endpoint warning (validated endpoint);
     the two warnings that quote response values (value withheld). The numeric
     status of each storage request is printed by the connection pool's request
     line, which carries it.
   - **Error-reporting SDK** (`sentry_sdk`): `Unexpected status code` (status;
     body withheld) and the `HTTP 413` line (body withheld).
   - **ASGI server access log** (`uvicorn.access`): the five-argument tuple its
     formatter unpacks (client, method, target, version, status).
6. Covered families: `anthropic`, `httpx`, `httpx2`, `httpcore`, `httpcore2`,
   `urllib3`, `requests`, `charset_normalizer`, `spotipy`, `boto3`,
   `botocore`, `s3transfer`, `sentry_sdk`, `pydantic_settings` (whose DEBUG
   output dumps settings values), `bs4` (which parses provider feed content),
   and `uvicorn.access`.
7. Tracebacks of known forms are rebuilt from the exception tree without
   source text or messages (file, line, function, validated class, with causes,
   contexts and exception group members, up to 100 frames per traceback); the
   configured secret values are then replaced. Cached traceback text that
   cannot be rebuilt is withheld, and stack text keeps its frame lines only.
   Because third-party records never print exception messages, the
   database-error pass is not run on them.
8. The boundary never raises into a logging call. On any internal failure, for
   any record, every content attribute is first set to a constant; then a
   placeholder naming the message type and exception classes is attempted; if
   that fails too, the constant `<unprintable: sanitiser failed>` remains.
9. Request lines stay at INFO. The default log level, the log format and what
   the application's own code logs are unchanged.

Not covered, and why:

- `sqlalchemy`: pinned at WARNING by the library; statement parameters are
  hidden by `hide_parameters`; database errors are withheld by 0008.
- `alembic`: migration progress lines from the application's own revisions.
- `psycopg`: its records carry connection descriptions without the password
  and database errors, which 0008 withholds; it has no notice logging enabled.
- `redis`: connection state and a repr naming host, port and database.
- `fastapi`, `anyio`, `uvicorn.error`: framework warnings and application
  errors, covered by 0008; no request or response material.
- `websockets`, `watchfiles`: the development server's reload and protocol
  support, not used by the application's endpoints.
- SMTP (`smtplib`): no logging; protocol traces only with `set_debuglevel`,
  which the application never sets.
- `feedparser`, `rapidfuzz`, `cryptography`, `email_validator`,
  `prometheus_client`, `pydantic`, `starlette`, `h11`: no logging calls.

## Consequences

- Operators see the request flow, statuses, request ids, rate-limit headers,
  retry and redirect notices, storage error codes and exception classes, at
  every level; every other third-party record shows its logger, level and size
  only. Diagnosing a library's own behaviour in depth needs a local run outside
  this boundary.
- A useful line from a covered library appears only after a known form is
  added for it, with a test; a library that logs on its own logger must be
  added to the families; a diagnostic header must be added to the allowlist.
- Credential-named keys such as `code`, `key` and `session` are masked
  wherever they appear as structured keys or query names; free text is never
  printed, so they cannot over-mask it.
- Unrecognised records cost a dictionary lookup and a marker; known forms are
  bounded by their output limits; application records cost one dictionary
  lookup on the logger name.

Known limits:

- Credentials read outside `Settings` (the LLM and Spotify SDKs read their own
  environment variables) are masked by name and structure only, not by value.
- A known form's validated parts can still contain a value that is sensitive in
  context while matching the conservative patterns (a short query value, an
  IAM role name).
- Attributes added to a record after it is created (`extra=` fields, which
  `Logger.makeRecord` sets after the record factory runs) are not inspected.
- Warnings captured through `logging.captureWarnings` are logged on the
  `py.warnings` logger, which is not covered.
- SQLAlchemy's DEBUG echo (`echo="debug"` or an explicit DEBUG level on its
  engine logger) prints result rows; it is an explicit opt-in and not covered.
- Records created without the record factory (`logging.makeLogRecord`) bypass
  the boundary; none of the covered libraries does this.
