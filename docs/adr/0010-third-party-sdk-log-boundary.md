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
record, and two filters covered two third-party loggers. The other clients log
on their own loggers and were not covered. Running each real client over a fake
transport at DEBUG showed request options with the prompt, request and response
headers, response bodies, provider reason phrases and error messages,
credential-provider responses, signing input with session tokens, and
connection errors whose traceback printed both the message and the source line
of the raising statement.

A first design cleaned every SDK record by structure and text rules and kept a
list of unsafe record forms needing special handling. Each review of it found
further DEBUG forms that carried provider text or credentials in a shape nobody
had listed. Enumerating unsafe forms does not converge: the libraries' DEBUG
output is large, changes between releases, and is written for their
maintainers, not for a log that may be shipped elsewhere.

## Decision

1. One boundary for every third-party SDK record, in the log record factory
   installed by `install_log_boundaries` (0008). It applies to the loggers in
   `SDK_LOGGER_FAMILIES` and their children: `anthropic`, `httpx`, `httpx2`,
   `httpcore`, `httpcore2`, `urllib3`, `requests`, `spotipy`, `boto3`,
   `botocore`, `s3transfer` and `uvicorn.access`. It replaces the two per-logger
   filters, is installed once however often logging is configured, and survives
   `basicConfig`, Alembic's `fileConfig` and the ASGI server's `dictConfig`.
   Records from other loggers (the application's own) are not changed. Because
   the SDK boundary prints exceptions by class only, it also withholds database
   text, and the database-error pass is not run on SDK records.
2. **DEBUG output of third-party libraries is allowlisted by form.** A record
   below INFO from a covered logger is written with content only if it matches
   a known record form whose handler builds a skeleton from parts that are safe
   by construction. Every other such record keeps its logger name and level,
   and its message becomes `[debug record withheld: unrecognised form, N
   characters]`; its arguments, traceback, cached traceback text and stack text
   are dropped. It is neither rendered nor traversed.
3. Known record forms (`backend/src/podex/sdk_log_forms.py`), at any level:
   - LLM SDK: `Request options: %s` (method, sanitised URL, parameter names,
     allowlisted headers, every other option withheld by type and size);
     `Sending HTTP Request: %s %s`; `HTTP Response: %s %s "%i %s" %s` (the
     provider's reason replaced by the standard phrase for the status, headers
     allowlisted); `request_id: %s` and `workspace_id: %s` (header allowlist);
     retry notices with numbers only; `Encountered Exception` and
     `Encountered httpx2.TimeoutException` (traceback rebuilt);
     `Could not read JSON from response data due to %s - %s` (class names);
     `Retrying request to %s in %f seconds`.
   - HTTP clients: `HTTP Request: %s %s "%s %d %s"` (standard reason phrase).
   - HTTP transports (`httpcore`, `httpcore2`) trace lines: step and outcome;
     a failed step keeps its exception class; `connect_tcp.started` keeps host
     and port; `receive_response_headers.complete` keeps protocol, numeric
     status and allowlisted headers and drops the reason phrase.
   - Connection pool (`urllib3`): new-connection lines, the request line
     (target without credential query values), `Resetting dropped connection`,
     `Retry: %s`, `Redirecting %s -> %s`.
   - Spotify SDK: `RESULTS:` (body size only), the `HTTP Error for ...` line
     (method, sanitised URL, parameter names, status; provider message
     withheld), the request and token lines (parameter names, allowlisted
     headers; body withheld).
   - Object storage client (`botocore`): `Sending http request: %s` (method,
     sanitised URL, allowlisted headers), `Response headers: %r` (allowlist),
     `Response body:\n%r` (size only), the `CanonicalRequest`, `StringToSign`
     and `String to sign` dumps (header lines allowlisted, query lines without
     credential values), `Signature:\n%s` (masked), signing and retry notices
     with constants or numbers, `Exception received when sending HTTP request.`
     (traceback rebuilt), and the two warnings that quote a response value
     (`"Expires" member` and the unknown tagged-union member; value withheld).
   - ASGI server access line: the five-argument tuple its formatter unpacks,
     with the request target's credential query values masked.
4. Other records at INFO and above keep structured cleaning plus the text
   rules, because their volume is small and operators need them: credential-
   named values masked (names compared lowercased without separators and after
   percent-decoding, including `accesskeyid`, `secretaccesskey`,
   `sessiontoken`), body-named values and bytes withheld, header containers
   allowlisted (a header container already rendered to text is withheld), URLs
   without credentials, exceptions by class; the message is rendered and the
   complete text scanned (credential fields, with authorization values masked
   scheme and credential together; wire header pairs; `exception=` reprs; URL
   userinfo; configured secret values). Every INFO-and-above record each SDK
   can emit was reviewed: those that quote provider-supplied values are known
   forms; the rest carry constants, configuration values, request URLs or
   exceptions, which print by class only.
5. Each processed record has a budget: 8,192 characters per string or
   rendered message (a longer value is withheld whole by type and size, never
   cut to a prefix), 32,768 characters of string values, 64 arguments, 64
   placeholders, 1,000 container items, 4 levels. A record past the budget
   becomes `[record withheld: exceeds the inspection budget, N characters]`.
6. Tracebacks of SDK records are rebuilt from the exception tree without
   source text or messages: file names, line numbers, function names and
   exception classes, with causes, contexts and exception group members, up to
   100 frames per traceback and the tree bounds of 0008; the configured secret
   values are then replaced. Cached traceback text that cannot be rebuilt
   becomes `[traceback text withheld]`, and stack text keeps its frame lines
   only. The original exception is detached from the record.
7. The boundary is fail-safe as 0008's is: it never raises into a logging call.
   On any internal failure, for any record, the message and any traceback text
   become the fixed placeholder, and the arguments, the attached exception and
   the stack text are cleared.
8. Request lines stay at INFO. The default log level, the log format and what
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
  statuses, request ids and rate-limit headers; everything else a library
  writes at DEBUG appears as a withheld marker with its logger and size.
  Diagnosing a library's own behaviour in depth needs a local run outside this
  boundary.
- A useful DEBUG line from a covered SDK appears only after a known form is
  added for it, with a test; a new SDK that logs on its own logger must be
  added to `SDK_LOGGER_FAMILIES`; a new diagnostic header must be added to the
  allowlist to be shown.
- Generous key matching masks some values that are not secret (an object
  storage `Key`, an idempotency key, a page token).
- Unrecognised DEBUG records cost a dictionary lookup and a marker; processed
  records are bounded by their budget; records from the application's loggers
  cost one dictionary lookup on the logger name.

Known limits:

- Credentials read outside `Settings` (the LLM and Spotify SDKs read their own
  environment variables) are masked by structure and key only, not by value.
- Records at INFO and above that are not known forms are covered by structured
  cleaning and the text rules, not by an allowlist.
- Attributes added to a record after it is created (`extra=` fields, which
  `Logger.makeRecord` sets after the record factory runs) are not inspected.
- Warnings captured through `logging.captureWarnings` are logged on the
  `py.warnings` logger, which is not in the families, and are not covered.
- SQLAlchemy's DEBUG echo (`echo="debug"` or an explicit DEBUG level on its
  engine logger) prints result rows; it is an explicit opt-in and not covered.
- Records created without the record factory (`logging.makeLogRecord`) bypass
  the boundary; none of the listed SDKs does this.
