<!-- markdownlint-disable MD041 -->

# 0011. Start-up errors carry no configured value

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
3. **A configured URL or DSN that cannot be used is reported by setting and
   error class only.** Each is parsed or used inside `without_setting_text`
   (`podex.unusable_settings`), which is told where the value comes from:
   - `PODEX_DATABASE__URL`: the application engine;
   - the migration environment's URL, named as `PODEX_DATABASE__URL` or, when
     `sqlalchemy.url` is set in the Alembic configuration, as that: the offline
     URL and dialect check and the online engine;
   - `PODEX_RATE_LIMIT__REDIS_URL`: the Redis client of the shared rate-limit
     store, the only Redis client built from configuration;
   - `PODEX_OBSERVABILITY__SENTRY_DSN`: error-reporting initialisation, in the
     API and the scheduler.

   A failure there of the kinds parsing, dialect and driver loading and client
   construction raise (SQLAlchemy errors, `ImportError`, `LookupError`,
   `TypeError`, `ValueError`, which includes the DSN's `BadDsn`) is replaced by
   `UnusableSettingError`, whose message names the source, the original class
   and, for a failed import, the missing module's name when it has a module
   name's form. It is raised outside the handler, so the original error is
   neither its cause nor its context. Other exceptions propagate unchanged. An
   empty Redis URL or DSN still selects the in-process limiter or leaves error
   reporting off, as before.

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

- When start-up fails, the printed text holds no input rejected by settings
  validation and no text of the settings point 3 lists. The settings library's
  debug records are withheld in every entrypoint.
- A validation error no longer shows the rejected value; the operator finds it
  from the field path. A URL or DSN error shows the class (`ValueError`,
  `BadDsn`, `NoSuchModuleError`, `ArgumentError`, `ModuleNotFoundError` with the
  module) but not the library's message, so the dialect name in "Can't load
  plugin" is not shown either.
- What would break the guarantee, and must not be done: constructing
  `Settings()` from the environment in an entrypoint instead of calling
  `get_settings()`; parsing a configured URL or DSN, or building an engine or
  client from one, outside `without_setting_text`; a settings model that does
  not inherit `SettingsSection` (a test fails); importing the settings or
  database module from the boundary's modules, which would resolve settings
  before the boundary is installed if resolution ever moved to import time
  there.
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
- Values other than those in point 3 are not parsed at start-up and are not
  guarded: the object storage endpoint and credentials, the transcript
  encryption key, the SMTP host and port, the WorkOS redirect URI, the billing
  checkout URLs, the CORS origins and the public web URL. The first two are used
  only when the artifact store is built (ops routes, retention purges), where a
  non-ASCII encryption key's `UnicodeEncodeError` prints one character of it.
