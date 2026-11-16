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
  the request body with the prompt), every response's headers, single response
  headers on their own records (`request_id: %s`, `workspace_id: %s`), and
  connection errors with their traceback and message.
- The LLM SDK's HTTP client (`httpx2`) logs every request URL at INFO,
  unfiltered, and both HTTP clients print the provider's reason phrase.
- The HTTP clients' connection layers (`httpcore`, `httpcore2`) log raw response
  header pairs and failed steps with the exception's repr; `urllib3` logs
  request paths with their query; the object storage client logs signing input
  (header lines including the session token and user metadata), the signature,
  request and response headers, the response body and send failures with their
  traceback; the ASGI server's access log, when enabled, logs the request path
  with its query.
- The Spotify SDK formats its own lines: request headers and bodies at DEBUG,
  the response body (`RESULTS: ...`) at DEBUG, and the provider's error message
  at ERROR, which is written at the default level.

The process log level is INFO; operators raise it to DEBUG when investigating a
provider problem, which is when most of the above is written.

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
   fields, numeric status, the allowlisted headers, and exception classes and
   frames.
3. Known record forms keep a fixed skeleton and withhold provider-supplied free
   text:
   - Spotify SDK lines: `RESULTS:` keeps only the body's size; the
     `HTTP Error for <method> to <url> with Params: ... returned <status>` line
     keeps method, sanitised URL, parameter names and status and withholds the
     provider's message; request lines keep method, sanitised URL, parameter
     names and allowlisted headers and withhold the body.
   - HTTP client request lines print the standard reason phrase for the status,
     not the provider's.
   - The ASGI access line keeps its five-argument tuple (its formatter unpacks
     it) with the request target's credential query values masked.
   - The LLM SDK's single-header records follow the header allowlist.
   - The storage client's `CanonicalRequest` and `StringToSign` dumps keep
     allowlisted header values only and mask credential query values.
4. Every other record is cleaned by structure and key, then rendered and
   scanned whole:
   - Arguments keep their shape. URLs lose userinfo and fragment and credential
     query values; header containers (typed header objects, values under a
     `headers` key or after a `... headers:` format label, request and response
     objects' `headers`) keep every name and only the values of an allowlist of
     diagnostic headers; mapping entries and format labels named like a
     credential are masked; body-named entries and bytes are withheld with
     their type and size; exceptions print their class only.
   - Credential names are matched lowercased with separators removed (`token`,
     `accesstoken`, `awsaccesskeyid`, `xamzsignature`, `clientsecret`, ...),
     with suffix rules (`...token`, `...secret`, `...password`, `...signature`,
     `...credential`, `...apikey`, and snake-case `_key`, `_auth`), after
     percent-decoding, wherever a name appears: structured keys, query strings
     and free text.
   - The message is then rendered from the cleaned arguments and the complete
     text passes the text rules: credential `name: value` and `name=value`
     fields, wire header pairs outside the allowlist, `exception=` reprs, URL
     userinfo, and the configured secret values of the loaded settings via the
     existing `SecretRedactor` (raw and percent-encoded; the API and the
     scheduler register them when they load settings). If this changes the
     text, the record's message becomes the sanitised text and its arguments
     are cleared; placeholders inside quotes or after a prefix
     (`'Authorization': '%s'`, `Bearer %s`) are covered this way.
   - A logged traceback keeps its frames, classes, chain and exception group
     members, every message withheld, and the original exception is detached
     from the record.
5. Scanning is linear and capped: each string or rendered message is inspected
   up to 8,192 characters (cut at a nearby delimiter, the rest replaced by a
   marker), and containers are read lazily up to 1,000 items; a container past
   the bound becomes a placeholder without being enumerated.
6. The boundary is fail-safe as 0008's is: it never raises into a logging call.
   On any internal failure, for any record, the message and any traceback text
   (attached or cached) become the fixed placeholder, and the arguments, the
   attached exception and the stack text are cleared.
7. Request lines stay at INFO. The default log level, the log format and what
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
  statuses, request ids and rate-limit headers without credentials, bodies,
  provider messages or error text; diagnosing a provider's own error message
  needs the application's failure descriptors or the provider's console.
- A new SDK that logs on its own logger must be added to `SDK_LOGGER_FAMILIES`;
  a new diagnostic header must be added to the allowlist to be shown; a new SDK
  line that carries provider text in a pre-formatted message needs a record
  form.
- Generous key matching masks some values that are not secret (an object
  storage `Key`, an idempotency key, a page token).
- Each SDK record costs a structural walk, a render and a few linear text
  passes; records from the application's loggers cost one dictionary lookup on
  the logger name.

Known limits:

- Credentials read outside `Settings` (the LLM and Spotify SDKs read their own
  environment variables) are masked by structure and key only, not by value.
- Free text that an SDK formats into a message the record forms do not know is
  covered by the text rules only (credential-named fields, header pairs,
  exception reprs, URL userinfo, configured values).
- A secret straddling the 8,192-character cut of a string with no delimiter in
  the last 256 characters before the cut can be partly printed.
- Attributes added to a record after it is created (`extra=` fields, which
  `Logger.makeRecord` sets after the record factory runs) are not inspected.
- Warnings captured through `logging.captureWarnings` are logged on the
  `py.warnings` logger, which is not in the families, and are not covered.
- SQLAlchemy's DEBUG echo (`echo="debug"` or an explicit DEBUG level on its
  engine logger) prints result rows; it is an explicit opt-in and not covered.
- Records created without the record factory (`logging.makeLogRecord`) bypass
  the boundary; none of the listed SDKs does this.
