<!-- markdownlint-disable MD041 -->

# 0012. First-use setting failures carry no configured value

- Status: proposed
- Date: 2026-11-16
- Tags: config, security, observability

Extends [0011](./0011-safe-startup-configuration-errors.md): it closes 0011's
known limit for first-use failures. 0011 is otherwise unchanged.

## Context

0011 makes start-up report an unparsable credential-bearing URL or DSN by
setting name and error class only. Some clients built from settings pass the
value on without using it, so a malformed value got past start-up and was quoted
by the library that first used it, one request or one ops action later:

- The shared rate-limit store's Redis client is built at start-up but connects
  on the first rate-limited request. Its URL's query options are first given to
  the connection class then: a certificate-requirements flag it does not know
  raised `RedisError` quoting the flag, an unknown option name raised
  `TypeError` quoting the name, and a port given as an option raised
  `ValueError` quoting it. The first was caught by the limiter's fail-open path
  and logged with its traceback on every request; the others failed the request.
- A connection that fails (an unresolvable host, a missing socket path) raised
  `ConnectionError` quoting the host and port or the path. The limiter failed
  open and logged it with its traceback. The log boundary (0008, 0010) withholds
  database error text and third-party records, not the text of other errors
  logged by the application's own loggers.
- The transcript artifact store is built by the ops purge and takedown routes.
  The object storage client quoted a malformed endpoint (`ValueError`) and
  region (`InvalidRegionError`), and encoding a non-ASCII encryption key raised
  `UnicodeEncodeError` quoting the offending character. All three are
  `ValueError`s, which those routes return as a 409 with the error's text. An
  endpoint with a non-numeric port, a scheme other than `http` or `https`, or a
  query passed construction and was then quoted on every bucket request
  (`EndpointResolutionError`).

Hosts, socket paths, endpoints and regions are not credentials, but an operator
may not want them in logs or responses, and query option names and the key can
hold credential text.

## Decision

1. **Each client's settings are checked when the client is built, under
   `without_setting_text`** (`podex.unusable_settings`, 0011 point 3), using the
   check the library itself applies on first use, so the setting is reported by
   name and error class only and the original error is neither chained nor kept
   as context:
   - `PODEX_RATE_LIMIT__REDIS_URL`: after the client is built from the URL as
     before, one connection object is constructed from the client's own pool
     (its connection class and connection arguments). That runs the client's own
     option handling without opening a socket; the object is discarded and the
     client is used unchanged. The classes reported are those the connection
     classes raise for bad options: `RedisError`, `TypeError` and `ValueError`.
   - `PODEX_TRANSCRIPTS__S3_ENDPOINT_URL`: the checks the client applies to an
     endpoint, before the client is built: its construction check (a valid host
     name, or a valid IPv6 address) and the S3 endpoint rules' URL check made on
     every bucket request (`http` or `https`, no query, a numeric port),
     reported as `ValueError`. An endpoint failing the second check could build
     a client but not serve any request, so no working configuration is
     rejected.
   - `PODEX_TRANSCRIPTS__S3_REGION_NAME`: the client's own region check,
     reported as its class (`botocore.exceptions.InvalidRegionError`).
   - `PODEX_TRANSCRIPTS__ENCRYPTION_KEY`: its ASCII encoding, reported as
     `UnicodeEncodeError`; both stores encode it once, through one helper.

   A setting is checked only when it is used: an empty Redis URL, endpoint or
   region is not checked, as before.

2. **Where a check runs is where the client is built.** The rate-limit store is
   built at start-up, so a bad Redis option now stops start-up, as an unparsable
   Redis URL already does. Previously the certificate flag disabled rate
   limiting on every request without stopping anything, and an unknown option or
   a port option failed every request; neither is an outage that could recover.
   The artifact store is still built per ops action, not at start-up: a bad
   storage setting must not stop the public API, which does not use the store.
3. **The ops purge and takedown routes return a setting failure as they returned
   the library's error**: 409, with the setting error's message (the setting
   name and class) as the detail.
4. **Connection failures at use time are logged by class only.** The limiter
   still fails open on `RedisError` and logs one warning per failed check
   (`... failing open for key=<client>: redis.exceptions.ConnectionError`),
   without the traceback; a failed cleanup scan is logged the same way.

Deliberately not chosen:

- **Checking the storage settings at start-up.** It would find a bad value
  sooner, but would stop the API and the scheduler for a setting only two ops
  routes use, and the scheduler never builds the store.
- **Reimplementing the Redis option rules** (known names per connection class,
  value types, certificate flags). They differ between connection classes and
  library versions; the library's own constructor is the authority.
- **Building the clients inside the guard.** A fault while building the client
  (0011's consequence) would be relabelled as a configuration error; the checks
  above run on their own, and the client is built outside them.
- **Teaching the log boundary to withhold Redis error text.** The limiter's
  warning is the only place it is logged, and it is changed at the source.

## Consequences

- With these settings malformed, the first request, the ops routes' responses,
  the raised error as printed and the logs name the setting and the error class
  and hold none of the value.
- Valid values reach the clients with the same arguments as before.
- A Redis URL whose options the connection class rejects (including an option
  the unix-socket connection does not take, such as `socket_keepalive`) now
  stops start-up instead of failing open or failing each request.
- The limiter's warning no longer carries a traceback; diagnosing a Redis outage
  relies on the error class and Redis' own logs.
- What would break the guarantee: building a client from one of these settings
  without the check above; logging a Redis error object or its traceback from
  the limiter; catching broader classes in a check than the library raises for
  bad input.

Known limits:

- Only the failures above are covered. A well-formed object storage endpoint
  that cannot be reached is still quoted by the storage client's connection
  error (`EndpointConnectionError`), which the ops routes do not catch. A region
  taken from the environment or AWS configuration, not from
  `PODEX_TRANSCRIPTS__S3_REGION_NAME`, is checked by the client itself.
- A Redis option the connection class accepts but cannot use (a value it only
  reads when connecting) is not checked.
