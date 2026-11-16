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
their own code installs the log boundary. A failure there was printed raw by the
interpreter:

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
2. **Validation errors never carry the input.** `Settings` and every nested
   settings model set `hide_input_in_errors`; an error prints the field path and
   the error type only. Pydantic takes this option from the outermost model
   being validated, so `Settings` covers its nested sections during resolution;
   each section (a shared `SettingsSection` base) sets it too for when it is
   validated on its own.
3. **An unusable database URL is reported by error class only.** Engine creation
   (the application engine, the migration environment's online engine) and the
   migration environment's offline URL check run inside `without_url_text`
   (`podex.database_url`). A failure there of the kinds URL parsing, dialect and
   driver loading and engine construction raise (SQLAlchemy errors,
   `ImportError`, `LookupError`, `TypeError`, `ValueError`) is replaced by
   `DatabaseUrlError`, whose message names the setting and the original class.
   It is raised outside the handler, so the original error is neither its cause
   nor its context. Other exceptions propagate unchanged.

Deliberately not chosen:

- **Installing the boundary on package import** (`podex/__init__.py`). It would
  also guarantee the order, but importing any `podex` module, from any tool or
  test, would replace the record factory and the exception hooks as a side
  effect. An error reporter could then never wrap the exception hook before the
  boundary, an order 0008 requires to work and its tests exercise.
- **Making the settings and the engine lazy.** Every existing import of
  `podex.database` (`engine`, `SessionLocal`, `get_db`) keeps its eager module
  attributes. Point 1 already orders the boundary before resolution, wherever
  resolution happens.
- **Sanitising validation errors in the exception hook.** The error is made safe
  where it is raised instead, so it is safe however it is printed.
- **Reordering `create_app`.** It resolves the settings before configuring the
  log format, as before; point 1 already installs the boundary at that call.

## Consequences

- No settings value or database URL text is printed when start-up fails, and the
  settings library's debug records are withheld in every entrypoint.
- A validation error no longer shows the rejected value; the operator finds it
  from the field path. A URL error shows the class (`ValueError`,
  `NoSuchModuleError`, `ArgumentError`, `ModuleNotFoundError`) but not
  SQLAlchemy's message, so the dialect name in "Can't load plugin" is not shown
  either.
- What would break the guarantee, and must not be done: constructing
  `Settings()` from the environment in an entrypoint instead of calling
  `get_settings()`; building an engine from a configured URL outside
  `without_url_text`; a settings model that does not inherit `SettingsSection`
  (a test fails); importing the settings or database module from the boundary's
  modules, which would resolve settings before the boundary is installed if
  resolution ever moved to import time there.
- A custom validator must not raise `ValueError` or `AssertionError` with the
  input in its message: pydantic shows the message even when inputs are hidden.
  Credential validators raise an error outside pydantic's
  (`SecretEncodingError`), as before.
- Calling `get_settings()` in a process installs the record factory and the
  exception hooks there, as calling `create_app` already did.
