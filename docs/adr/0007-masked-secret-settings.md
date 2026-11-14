<!-- markdownlint-disable MD041 -->

# 0007. Secret settings are masked types; reports redact free text

- Status: proposed
- Date: 2026-11-14
- Tags: config, security, observability

## Context

The error reporter removes credentials by field name: request headers, query
strings, local variables and a key denylist. Values inside free text were not
covered. Exception messages embedding request URLs (userinfo and query keys),
database errors rendering statement parameters, HTTP client log lines with URL
userinfo, and the `repr` of `Settings` could all carry a secret into a log line
or a report, whatever field name surrounded it.

## Decision

1. Every credential in `backend/src/podex/config.py` is a pydantic `SecretStr`:
   API keys, client and webhook secrets, the ops key, the SMTP password, storage
   and encryption keys, and the database and Redis URLs (which can embed a
   password). `repr`, `str` and serialisation mask them. Each use reads the
   value explicitly with `get_secret_value()`. Field and environment variable
   names are unchanged.
2. A test enumerates settings fields whose names suggest a credential, plus the
   credential-bearing URL fields, and fails if any is not `SecretStr`.
3. Secrets are kept out of free text at the source: database engines set
   `hide_parameters`; providers that accept a header take the key there (Google
   Books uses `x-goog-api-key`); enrichment providers log an allowlisted
   descriptor (status or error class) for HTTP failures; the HTTP client log
   filter drops URL userinfo and fragments.
4. As a last resort, the reporter's `before_send` and `before_breadcrumb`
   replace URL userinfo and the known secret values of the loaded settings (raw
   and URL-encoded) in every string, and breadcrumb header data keeps only the
   allowlisted request headers. Values shorter than eight characters are not
   matched.

Deliberate non-choices: no log-wide value scrubbing (sources are fixed instead);
credentials read outside `Settings` (Spotify discovery reads its own environment
variables) are not covered by the value scrub; the Sentry DSN stays a plain
string, because its key is public by design.

## Consequences

- Reading a secret requires an explicit call at each use. A missed call fails
  type checks or tests rather than sending a masked value.
- A new credential setting must be declared `SecretStr`, or the enumeration test
  fails.
- Constructing settings in code accepts plain strings; copying a model with
  `model_copy(update=...)` skips validation, so updates must pass a `SecretStr`.
- Report text that happens to contain a configured secret value is shown as
  `[redacted]`.
