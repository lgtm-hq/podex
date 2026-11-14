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
Spotify SDK log lines, and the `repr` of `Settings` could all carry a secret
into a log line or a report, whatever field name surrounded it.

## Decision

1. Every credential in `backend/src/podex/config.py` is a pydantic `SecretStr`:
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
     values) is withheld at the logging boundary: a log record factory installed
     by `configure_logging` formats any traceback whose chain holds a database
     error with that error's class and SQLSTATE (or SQLite error name) instead
     of its message, and replaces database errors passed as log arguments the
     same way. This covers every logger, including the ASGI server's. Reports
     carry the same descriptor in place of the message.
   - Providers that accept a header take the key there (Google Books uses
     `x-goog-api-key`); enrichment providers log an allowlisted descriptor
     (status or error class) for HTTP failures.
   - The HTTP client log filter drops URL userinfo and fragments; a filter on
     the Spotify SDK's loggers masks authorization headers and token fields.
   - The Alembic environment escapes `%` in the database URL before storing it
     as a ConfigParser option.
4. As a last resort, the reporter's `before_send` and `before_breadcrumb`
   replace URL userinfo and the known secret values of the loaded settings in
   every string, and breadcrumb header data keeps only the allowlisted request
   headers. Every non-empty secret is matched, however short; each secret
   shorter than 8 characters is named by its settings path in one warning when
   the reporter starts. URL handling is decided by field: the database and Redis
   URL settings contribute their password; every other secret is matched as its
   whole value. Values are matched raw and percent-encoded, with upper- or
   lowercase escapes.

Deliberate non-choices: no log-wide secret value scrubbing (sources are fixed
instead); no rejection of short secrets at load, which would change which
configurations start; credentials read outside `Settings` (Spotify discovery
reads its own environment variables) are not in the value scrub; the Sentry DSN
stays a plain string, because its key is public by design.

Known limits of the value scrub: JSON-escaped, base64 or case-changed forms of a
secret are not matched, and the secrets are those of the settings the reporter
was initialised with.

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
