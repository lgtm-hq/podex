<!-- markdownlint-disable MD041 -->

# 0008. Secret settings are masked types; reports redact free text

- Status: proposed
- Date: 2026-11-14
- Tags: config, security, observability

## Context

The error reporter removes credentials by field name: request headers, query
strings, local variables and a key denylist. Values inside free text were not
covered. Exception messages embedding request URLs (userinfo and query keys),
database errors rendering statement parameters or row values, HTTP client and
Spotify SDK log lines, and the `repr` of `Settings` could carry a secret into a
log line or a report, whatever field name surrounded it.

## Decision

1. Credentials in `backend/src/podex/config.py` are pydantic `SecretStr` fields:
   API keys, client and webhook secrets, the ops key, the SMTP password, storage
   and encryption keys, and the database and Redis URLs (which can embed a
   password). `repr`, `str` and serialisation mask them. Each use reads the
   value explicitly with `get_secret_value()`. Field and environment variable
   names are unchanged.
2. A test enumerates settings fields whose names suggest a credential, plus the
   credential-bearing URL fields, and fails if any is not `SecretStr`.
3. Secrets are kept out of free text at the source:
   - The application engine and Alembic's migration engine set
     `hide_parameters`.
   - Database error text (statement, parameters, driver `DETAIL` with row
     values) is withheld at the logging boundary. `install_log_boundaries`,
     called by each process entrypoint (the API's `create_app`, the scheduler
     runner, the Alembic environment), installs a log record factory and process
     and thread exception hooks. Wherever a database error appears in a record
     (its traceback, including exception groups and their causes and contexts;
     the message; arguments, mapping arguments and nested containers) or in an
     uncaught exception, its message is replaced by its class and SQLSTATE (or
     SQLite error name); the rest of the traceback is unchanged. Reports carry
     the same descriptor in place of the message. The exception hooks pass a
     sanitised copy of the exception to the hook installed before them, so an
     error reporter's hook still reports it, in either installation order.
   - The boundary is fail-safe. Whatever fails inside it, it never raises into a
     logging call or out of an exception hook and never falls back to the
     original text: the content is replaced by a fixed placeholder
     (`<unprintable: sanitiser failed on TypeName>`) followed by the exception
     class names. Traversal is bounded (256 exceptions per tree, 64 links when
     rebuilding, 4 levels and 1,000 items per log record's arguments); past a
     bound, the remainder is a placeholder
     (`<unprintable: sanitiser limit reached on TypeName>`), and a tree walk cut
     short is treated as holding a database error. Containers holding nothing to
     sanitise are not copied.
   - Providers that accept a header take the key there (Google Books uses
     `x-goog-api-key`); enrichment providers log an allowlisted descriptor
     (status or error class) for HTTP failures.
   - The HTTP client log filter drops URL userinfo and fragments; a filter on
     the Spotify SDK's loggers masks authorization headers and token fields.
   - The Alembic environment escapes `%` in the database URL before storing it
     as a ConfigParser option.
4. As a last resort, the reporter's `before_send` and `before_breadcrumb`
   replace URL userinfo and the known secret values of the loaded settings in
   free-text and data-bearing fields: the message, transaction, culprit, server
   name and logger name; exception values and source context; breadcrumb
   messages and data (including tuples and sets); `extra`, tags, the
   fingerprint, contexts other than the SDK's own, and request and user data.
   Breadcrumb data keeps only the allowlisted request headers at any depth. If
   minimising fails, a placeholder event (identifiers, level, timestamp, release
   and SDK metadata, and a placeholder message with the exception class names)
   or placeholder breadcrumb is sent instead. Reported database errors are
   matched to their own exceptions, so errors of one class keep their own
   SQLSTATE.

   Never rewritten, because their values are schema-constrained or come from
   code and the runtime rather than from application data, and rewriting them
   could make an event invalid when a short secret matches: `event_id`,
   `timestamp`, `start_timestamp`, `level`, `platform`, `release`, `dist`,
   `environment`, `sdk`, `modules`; exception types, modules and mechanisms;
   frame filenames, functions, modules and line numbers; the SDK-generated
   contexts `trace`, `runtime`, `os`, `device`, `app`, `cloud_resource` and
   `otel`; breadcrumb type, category, level and timestamp; and mapping keys.

   - Every non-empty secret is matched, however short; each setting with a value
     shorter than 8 characters is named by its path in one warning when the
     reporter starts.
   - URL handling is decided by field: the database and Redis URL settings
     contribute their password and their credential query options (`password`,
     `sslpassword`, `ssl_password`); every other secret is matched as its whole
     value.
   - Values are matched raw and percent-encoded, each escape's hex digits in
     either case; a value without a UTF-8 encoding is matched raw only.
   - URL userinfo is recognised with the full RFC 3986 character set, but not
     across the quote that opened the URL.

Deliberate non-choices: no log-wide secret value scrubbing (sources are fixed
instead); no rejection of short secrets at load, which would change which
configurations start; credentials read outside `Settings` (Spotify discovery
reads its own environment variables) are not in the value scrub; the Sentry DSN
stays a plain string, because its key is public by design.

Known limits:

- The value scrub does not match JSON-escaped, base64 or case-changed forms of a
  secret, and its secrets are those of the settings the reporter was initialised
  with.
- Text formatted into a string before it reaches the logger (for example an
  f-string that renders an error) is not covered by the logging boundary; only
  the value scrub applies to it in reports.
- Error text persisted in `ingestion_runs.error_summary` (and the scheduled work
  error message) and returned by the ops API is not covered here; it is handled
  separately.
- asyncio's "Task exception was never retrieved" message embeds a pre-formatted
  representation of the exception, so a database error ending an unawaited task
  is not covered by the logging boundary.
- Arbitrary objects in log arguments are not inspected: an object whose
  representation renders a database error is printed as it renders itself.

## Consequences

- Reading a secret requires an explicit call at each use. A missed call fails
  type checks or tests rather than sending a masked value.
- A new credential setting must be declared `SecretStr`, or the enumeration test
  fails.
- Constructing settings in code accepts plain strings; copying a model with
  `model_copy(update=...)` skips validation, so updates must pass a `SecretStr`.
- Report text that happens to contain a configured secret value is shown as
  `[redacted]`; with a short secret this can redact unrelated text.
- Logged database failures show the error class and SQLSTATE, not the driver's
  message; diagnosing them relies on the code and the statement's origin.
- A new process entrypoint must call `install_log_boundaries` before it logs.
