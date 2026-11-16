<!-- markdownlint-disable MD041 -->

# 0010. Third-party SDK log records pass one credential boundary

- Status: proposed
- Date: 2026-11-16
- Tags: security, observability, logging

Extends [0008](./0008-masked-secret-settings.md): it replaces 0008's two
per-logger filters (HTTP client request lines, Spotify SDK debug lines) and
narrows its non-choice of log-wide secret value scrubbing for third-party
records. 0008 is otherwise unchanged.

## Context

The application's log boundary (0008) withholds database error text from every
record, and two filters covered two third-party loggers: the HTTP client's
request line lost URL userinfo and the query fields `key`, `api_key` and
`apikey`, and the Spotify SDK's loggers lost authorization headers and token
fields. The other clients log on their own loggers and were not covered:

- With DEBUG enabled, the LLM SDK logs its request options (headers, query and
  the request body with the prompt), every response's headers, and connection
  errors with their traceback and message.
- The LLM SDK's HTTP client (`httpx2`) logs every request URL at INFO,
  unfiltered; the HTTP client's line kept credential query fields other than the
  three listed (`token=`, `access_token=`, signatures).
- The HTTP clients' connection layers (`httpcore`, `httpcore2`) log raw response
  header pairs and failed steps with the exception's repr; `urllib3` logs
  request paths with their query; the object storage client logs signing input
  (with the session token), the signature, request and response headers, the
  response body and send failures with their traceback; the ASGI server's
  access log, when enabled, logs the request path with its query.

The process log level is INFO; operators raise it to DEBUG when investigating a
provider problem, which is when all of the above is written.

## Decision

1. One boundary for every third-party SDK record, in the log record factory
   installed by `install_log_boundaries` (0008). It applies to the loggers in
   `SDK_LOGGER_FAMILIES` and their children: `anthropic`, `httpx`, `httpx2`,
   `httpcore`, `httpcore2`, `urllib3`, `requests`, `spotipy`, `boto3`,
   `botocore`, `s3transfer` and `uvicorn.access`. The two per-logger filters are
   removed. Because it lives in the record factory, it covers child loggers, is
   installed once however often logging is configured, and survives
   `basicConfig`, Alembic's `fileConfig` and the ASGI server's `dictConfig`.
   Records from other loggers (the application's own) are not changed.
2. Mask, do not drop. A record keeps its method, scheme, host, path, other query
   fields, status, the allowlisted headers, and exception classes and frames.
   Decisions are made by structure and by key where the SDK passes structured
   arguments:
   - URLs (`httpx.URL`, `httpx2.URL`, request objects' `url`) lose userinfo and
     fragment (`[redacted]`), and credential query values.
   - Header containers (typed header objects, values under a `headers` key or
     after a `... headers:` format label, request and response objects'
     `headers`) keep every name and only the values of an allowlist of
     diagnostic headers (content type and length, date, request ids, retry and
     rate-limit headers, the storage client's signing metadata).
   - Mapping entries and format labels (`"Signature:\n%s"`) whose name is a
     credential are masked: a generous key list (`token`, `access_token`,
     `auth`, `secret`, `signature`, `sig`, `password`, `key`, `api_key`,
     `code`, ...) plus names ending in `_token`, `_key`, `_secret`,
     `_password`, `_signature`, `_credential` or `_auth`, in any case, with `-`,
     `.` or camel case. Entries named like a body (`body`, `json_data`,
     `content`, `data`, ...) and bytes are withheld with their type and size.
   - Exceptions print as their class with `[message withheld]`; a logged
     traceback keeps its frames, classes and chain, every message withheld, and
     the original exception is detached from the record.
   - Arguments keep their shape (formatters such as the ASGI server's access
     formatter unpack them); other objects are rendered as their placeholder
     would render them and the text is cleaned.
3. Where an SDK formats its message itself, text rules apply to it, to string
   arguments and to the message's literal text: credential `name: value` and
   `name=value` fields, wire header pairs outside the allowlist, `exception=`
   reprs, URL userinfo, and the configured secret values of the loaded settings
   via the existing `SecretRedactor` (raw and percent-encoded). The API and the
   scheduler register those values when they load settings.
4. The boundary is fail-safe as 0008's is: it never raises into a logging call,
   and on internal failure the record's message, arguments and traceback become
   the fixed placeholder and the exception is detached. Traversal is bounded (4
   levels, 1,000 items per record).
5. Request lines stay at INFO. The default log level, the log format and what
   the application's own code logs are unchanged.

Not filtered, because they emit no request or response material: Redis (logs
connection state and the connection's repr, which names host, port and
database, never the password or commands), SMTP (the standard library prints
protocol traces only when a debug level is set, which the application never
does; it does not use logging), and the SQLAlchemy engine and pool (their
loggers are pinned at WARNING by the library, so raising the root level does not
enable them; statement parameters are hidden by `hide_parameters`, and
connection reprs name no password).

## Consequences

- Turning DEBUG on for a provider investigation shows the request flow,
  statuses, request ids and rate-limit headers without credentials, bodies or
  error text; diagnosing a provider error message needs the application's own
  failure descriptors or the provider's console.
- A new SDK that logs on its own logger must be added to `SDK_LOGGER_FAMILIES`;
  a new diagnostic header must be added to the allowlist to be shown.
- Generous key matching masks some values that are not secret (an object
  storage `Key`, an idempotency key).
- Each SDK record costs a structural walk and a few regular-expression passes;
  records from the application's loggers cost one dictionary lookup on the
  logger name.

Known limits:

- Provider text that an SDK formats into its own message (the Spotify SDK's
  `RESULTS: ...` debug line and its error line quoting the provider's error
  message) is kept unless it is a credential-named field, URL userinfo or a
  configured secret value. Credentials read outside `Settings` (the LLM and
  Spotify SDKs read their own environment variables) are masked by structure and
  key only, not by value.
- SQLAlchemy's DEBUG echo (`echo="debug"` or an explicit DEBUG level on its
  engine logger) writes result rows; it is an explicit opt-in and not covered.
- Records created without the record factory (`logging.makeLogRecord`) bypass
  the boundary; none of the listed SDKs does this.
