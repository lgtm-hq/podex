<!-- markdownlint-disable MD041 -->

# 0011. Start-up errors carry no credential

- Status: proposed
- Date: 2026-11-16
- Tags: config, security, observability

Extends [0008](./0008-masked-secret-settings.md) and
[0010](./0010-third-party-sdk-log-boundary.md): it closes 0010's known limit for
diagnostics the settings library emits before an entrypoint installs the
boundary. Both are otherwise unchanged.

## Context

`podex.database` resolves the settings and builds the engine when it is
imported, and the API, the scheduler and the OpenAPI generator import it before
their own code installs the log boundary. The API then builds the shared
rate-limit store's Redis client and initialises error reporting while
`podex.main` is imported, and the scheduler initialises error reporting when it
starts. A failure on any of these paths was printed raw by the interpreter:

- A settings validation error echoed the rejected input (`input_value=...`). A
  credential given in the wrong shape (a database or Redis URL given where its
  section is expected, an API key, SMTP password or encryption key that is not a
  string) printed the credential, from every entrypoint, including the migration
  environment, which installs the boundary itself but does not sanitise
  validation errors.
- SQLAlchemy reads an unescaped `@` or `:` in a URL password as the end of the
  userinfo and then fails on the rest of the password as a port number; its
  `ValueError` quoted that part of the password. Other unusable URLs failed with
  raw dialect or driver-loading errors.
- The Redis client's URL parser quoted the port text (where an unescaped `/`,
  `#` or `?` in the password moves the rest of it) and query option values.
- The error-reporting SDK's DSN parser quoted the project, the scheme and the
  port text.
- Reading `sqlalchemy.url` from the Alembic configuration interpolates it; a
  lone `%` (a percent-encoded password) or a `%(name)s` reference failed with
  the URL in the configuration parser's error.
- With the settings library's debug switch on and DEBUG logging configured
  before import, the library logged every value it read, credentials included,
  before any boundary was installed.

An uncaught exception at start-up is printed by the interpreter, not by logging,
so the boundary alone cannot make it safe.

## Decision

1. **Settings resolution installs the boundary first.** `get_settings()`, the
   only path that reads settings from the environment, calls
   `install_log_boundaries()` before it constructs `Settings`. Whatever the
   settings library logs while it reads the environment passes the boundary,
   whichever entrypoint or import resolves the settings first. Engine creation
   needs the resolved settings, so it runs after the boundary too. The
   boundary's own modules do not import the settings or the database module.
2. **The printed text of a validation error carries no input.** `Settings` and
   every nested settings model set `hide_input_in_errors`; the error's text
   (`str`, `repr`, the printed traceback) shows the field path and the error
   type only. Pydantic takes this option from the outermost model being
   validated, so `Settings` covers its nested sections during resolution; each
   section (a shared `SettingsSection` base) sets it too for when it is
   validated on its own.
3. **A credential-bearing URL or DSN that cannot be parsed is reported by
   setting and error class only.** Each is parsed on its own, with its library's
   own parser, inside `without_setting_text` (`podex.unusable_settings`), which
   is told where the value comes from and which classes that parser raises for
   bad input:
   - `PODEX_DATABASE__URL`: SQLAlchemy's `make_url`, the dialect and the driver
     import (`ArgumentError`, which includes `NoSuchModuleError`; `ValueError`;
     `ImportError`), for the application engine;
   - the migration environment's URL, named as `PODEX_DATABASE__URL` or, when
     `sqlalchemy.url` is set in the Alembic configuration, as that: the first
     read of the option (the configuration parser's `InterpolationError`), then
     the same URL check, without the driver offline;
   - `PODEX_RATE_LIMIT__REDIS_URL`: the client's own `parse_url` (`ValueError`),
     for the shared rate-limit store, the only Redis client built from
     configuration;
   - `PODEX_OBSERVABILITY__SENTRY_DSN`: the SDK's own `Dsn` parser
     (`ValueError`, which includes `BadDsn`), in the API and the scheduler.

   The engine, the client and the error reporter are then built from the
   validated value outside the guard, so a fault there (a `TypeError` or
   `KeyError` inside initialisation) propagates as itself. A parser failure is
   replaced by `UnusableSettingError`, whose message is built from fixed parts:
   the source, the failure's module-qualified class name validated as an
   identifier with a length bound (`[withheld]` otherwise), and, for a missing
   driver the application can need (`psycopg`, its compiled implementations,
   `sqlite3`), that module's name; any other module name is omitted. It is
   raised outside the handler, so the original error is neither its cause nor
   its context. An empty Redis URL or DSN still selects the in-process limiter
   or leaves error reporting off, as before.

Deliberately not chosen:

- **Installing the boundary whenever any `podex` module is imported**
  (`podex/__init__.py`). Importing any module, from any tool or test, would
  replace the record factory and the exception hooks, and an error reporter
  could then never wrap the exception hook before the boundary, an order 0008
  requires to work and its tests exercise. What happens instead: the boundary is
  installed when settings are first resolved. Importing `podex.database`
  resolves them, so importing it, or anything that imports it (`podex.main`, the
  scheduler runner, the OpenAPI generator, the API's dependency modules),
  installs the boundary as a side effect of that import. Importing the settings
  module or the boundary's own modules does not.
- **Making the settings and the engine lazy.** Every existing import of
  `podex.database` (`engine`, `SessionLocal`, `get_db`) keeps its eager module
  attributes. Point 1 already orders the boundary before resolution, wherever
  resolution happens.
- **Sanitising validation errors in the exception hook.** The error is made safe
  where it is raised instead, so its printed text is safe however it is printed.
- **Reordering `create_app`.** It resolves the settings before configuring the
  log format, as before; point 1 already installs the boundary at that call.

## Consequences

- The guarantee covers the credential-bearing settings: `PODEX_DATABASE__URL`
  (and `sqlalchemy.url` in the Alembic configuration),
  `PODEX_RATE_LIMIT__REDIS_URL`, `PODEX_OBSERVABILITY__SENTRY_DSN`,
  `PODEX_OPS_API_KEY`, `PODEX_AUTH__SMTP_PASSWORD`,
  `PODEX_AUTH__WORKOS_API_KEY`, `PODEX_BILLING__PADDLE_API_KEY`,
  `PODEX_BILLING__PADDLE_WEBHOOK_SECRET`, `PODEX_TRANSCRIPTS__ENCRYPTION_KEY`,
  `PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID`,
  `PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY` and the enrichment API keys
  (`PODEX_ENRICHMENT__{TMDB,OMDB,GOOGLE_BOOKS,PUBMED,SEMANTIC_SCHOLAR}__API_KEY`).
  When start-up fails, the printed text holds no input of these rejected by
  settings validation and no text of the URLs and DSN point 3 parses; the other
  keys are not parsed at start-up. The settings library's debug records are
  withheld in every entrypoint.
- A validation error no longer shows the rejected value; the operator finds it
  from the field path. A URL or DSN error shows the class (`ValueError`,
  `sentry_sdk.utils.BadDsn`, `sqlalchemy.exc.NoSuchModuleError`,
  `sqlalchemy.exc.ArgumentError`, `configparser.InterpolationSyntaxError`,
  `ModuleNotFoundError`, with the module for an application driver) but not the
  library's message, so the dialect name in "Can't load plugin" is not shown
  either.
- What would break the guarantee, and must not be done: constructing
  `Settings()` from the environment in an entrypoint instead of calling
  `get_settings()`; building an engine or client from a credential-bearing URL
  or DSN that was not first parsed under `without_setting_text`; catching
  broader classes there than the parser raises for bad input, which would
  relabel faults as configuration errors; a settings model that does not inherit
  `SettingsSection` (a test fails); importing the settings or database module
  from the boundary's modules, which would resolve settings before the boundary
  is installed if resolution ever moved to import time there.
- A custom validator must not raise `ValueError` or `AssertionError` with the
  input in its message: pydantic shows the message even when inputs are hidden.
  Credential validators raise an error outside pydantic's
  (`SecretEncodingError`), as before.
- Resolving settings in a process installs the record factory and the exception
  hooks there, as calling `create_app` already did.

Known limits:

- Only the printed text of a validation error is clean. The error object still
  holds the input: `errors()` and `json()` return it, and when the settings
  library fails to parse an environment value (`SettingsError`), the cause it
  chains (for example a `JSONDecodeError`, whose `doc` is the whole value) holds
  it. A traceback rendered with local variables would show it too. Nothing
  prints these at start-up today, and the error reporter is initialised with
  local-variable capture off.
- Non-credential settings are not covered. A malformed one may be echoed by the
  library that parses it: for example an API prefix with malformed path
  parameters is quoted by the router when the OpenAPI schema is generated.
- The error reporter's own transport warnings print the request path, which
  includes the DSN's path component (the project path), never its key. This
  follows the URL-path limit stated in 0010.
- First-use failures, one request or one piece of work after start-up, are not
  covered here and are tracked separately: the Redis client connects lazily, and
  its connection errors can quote the host, the socket path or an unknown query
  option; building the object storage client can quote a malformed endpoint or
  region; a transcript encryption key that is not ASCII is quoted in part by the
  encoding error.
