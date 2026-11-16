<!-- markdownlint-disable MD041 -->

# 0013. First-use setting failures carry no configured value

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
  `ValueError` quoting it. Other options are accepted by the connection class
  and fail only when used: an unknown `encoding` raised `LookupError` quoting it
  when the first command was encoded, and a TLS `ssl_min_version` the TLS
  library does not know raised `ValueError` quoting the number when the first
  connection was wrapped. The certificate flag was caught by the limiter's
  fail-open path and logged with its traceback on every request; the others
  failed every request with the value in the error.
- A connection that fails (an unresolvable host, a missing socket path) raised
  `ConnectionError` quoting the host and port or the path. The limiter failed
  open and logged it with its traceback. The log boundary (0008, 0010) withholds
  database error text and third-party records, not the text of other errors
  logged by the application's own loggers.
- The transcript artifact store is built by the ops purge and takedown routes.
  An unknown storage backend (`ValueError` from the backend enum), a malformed
  endpoint (`ValueError`) or region (`InvalidRegionError`, also for a region
  taken from the AWS environment or configuration), and a non-ASCII encryption
  key (`UnicodeEncodeError`) were all quoted. All are `ValueError`s, which those
  routes return as a 409 with the error's text.
- Settings that built a client were then quoted by the client on every bucket
  request: an endpoint with a non-numeric port, a scheme other than `http` or
  `https`, or a query (`EndpointResolutionError`); a bucket name it rejects
  (`ParamValidationError`); a static access key id holding a control character,
  such as a trailing newline read from a secrets file (`HTTPClientError` quoting
  the whole signed `Authorization` header); and an endpoint that cannot be
  reached (`EndpointConnectionError`, quoting the URL with any userinfo). These
  are not `ValueError`s; the routes answered 500, and the server logged the
  error with its text.

Hosts, socket paths, endpoints, buckets and regions are not credentials, but an
operator may not want them in logs or responses, and query option names, the
key, the access key id and URL userinfo can hold credential text.

## Decision

1. **Each client's settings are checked when the client is built, under
   `without_setting_text`** (`podex.unusable_settings`, 0011 point 3), using the
   check the library itself applies on first use, so the setting is reported by
   name and error class only and the original error is neither chained nor kept
   as context:
   - `PODEX_RATE_LIMIT__REDIS_URL`: after the client is built from the URL as
     before, one connection object is constructed from the client's own pool
     (its connection class and connection arguments), never added to the pool
     and never connected. On it, a command with a text argument is encoded as
     the first request would encode one (the configured `encoding`), and for TLS
     the configured `ssl_min_version` is set on a fresh TLS client context as
     the connection would set it. The classes reported are those these steps
     raise for bad options: `RedisError`, `LookupError`, `TypeError` and
     `ValueError`.
   - `PODEX_TRANSCRIPTS__STORAGE_BACKEND`: its conversion to the backend enum,
     reported as `ValueError`.
   - `PODEX_TRANSCRIPTS__S3_BUCKET`: the client's own bucket name check
     (`ParamValidationError`).
   - `PODEX_TRANSCRIPTS__S3_ENDPOINT_URL`: the checks the client applies to an
     endpoint, before the client is built: its construction check (a valid host
     name, or a valid IPv6 address) and the S3 endpoint rules' URL check made on
     every bucket request (`http` or `https`, no query, a numeric port),
     reported as `ValueError`.
   - `PODEX_TRANSCRIPTS__S3_REGION_NAME`: the client's own region check,
     reported as its class (`botocore.exceptions.InvalidRegionError`). With the
     setting empty, the client takes its region from the AWS environment or
     configuration and checks it while it is built; building the client catches
     only that class, reported with the source "The AWS region from the
     environment or AWS configuration".
   - `PODEX_TRANSCRIPTS__S3_ACCESS_KEY_ID` and
     `PODEX_TRANSCRIPTS__S3_SECRET_ACCESS_KEY`: no control characters
     (`ValueError`).
   - `PODEX_TRANSCRIPTS__ENCRYPTION_KEY`: its ASCII encoding, reported as
     `UnicodeEncodeError`; both stores encode it once, through one helper.

   A setting is checked only when it is used: an empty Redis URL, endpoint,
   region or credential is not checked, as before.

2. **Where a check runs is where the client is built.** The rate-limit store is
   built at start-up, so a bad Redis option now stops start-up, as an unparsable
   Redis URL already does. Previously these options disabled rate limiting or
   failed every request; neither could recover without a configuration change.
   The artifact store is still built per ops action, not at start-up: a bad
   storage setting must not stop the public API, which does not use the store.
3. **The ops purge and takedown routes return a setting failure as they returned
   the library's `ValueError`**: 409, with the setting error's message (the
   setting name and class) as the detail. Both routes build the store before
   they look anything up, so this includes actions that would not have reached
   the bucket (see the behaviour change under Consequences).
4. **Use-time failures are reported by class only, with today's fail-open and
   fail-closed behaviour.**
   - The limiter still fails open on `RedisError` and logs one warning per
     failed check, without the traceback, ending in the error class and its
     cause's class and numeric `errno`, for example
     `redis.exceptions.ConnectionError (cause socket.gaierror, errno <n>)`.
     These are value-free and tell an unknown host from a missing socket
     (`FileNotFoundError, errno 2`); the resolver's number is platform-specific.
     A failed cleanup scan is logged the same way.
   - Any other error the client raises during a check (`AttributeError`,
     `LookupError`, `TypeError`, `ValueError`; for example an option that
     slipped past the start-up check) still fails the request, as
     `RateLimitStoreError` naming the class only, raised outside the handler.
     Redis errors that are also `ValueError`s (lock errors) still fail open.
   - Each request of the encrypted S3 store (put, get with its body read,
     delete) turns the client's own errors (`BotoCoreError`: parameter
     validation, connection, endpoint resolution, HTTP client, credentials) into
     `TranscriptStorageError` naming the class only, raised outside the handler.
     It derives from `Exception` only, as the client's errors do, so the
     verification step and the ops routes treat it as before: the routes answer
     500 with the opaque problem body, and the server's error record shows the
     class only. A server's error response (`ClientError`) is not a client-side
     error and propagates unchanged.

Deliberately not chosen:

- **Checking the storage settings at start-up.** It would find a bad value
  sooner, but would stop the API and the scheduler for a setting only two ops
  routes use, and the scheduler never builds the store.
- **Reimplementing the Redis option rules** (known names per connection class,
  value types, certificate flags, codec names). They differ between connection
  classes and library versions; the library's own code is the authority.
- **Building the clients inside the guard**, beyond the region case above. A
  fault while building the client (0011's consequence) would be relabelled as a
  configuration error; the checks above run on their own, and the client is
  built outside them. Building the storage client catches only
  `InvalidRegionError`, which the client raises for nothing but its region.
- **Teaching the log boundary to withhold Redis or storage error text.** Both
  are replaced where they are raised or logged, so they are safe however they
  are printed.
- **Rewrapping server error responses.** A `ClientError` carries the server's
  code, which callers and operators need; see the known limits.

## Consequences

- With these settings malformed, the first request, the ops routes' responses,
  the raised error as printed and the logs name the setting and the error class
  and hold none of the value.
- Valid values reach the clients with the same arguments as before.
- A Redis URL whose options the connection class rejects or cannot apply
  (including an option the unix-socket connection does not take, such as
  `socket_keepalive`) now stops start-up instead of failing open or failing each
  request.
- The limiter's warning no longer carries a traceback; diagnosing a Redis outage
  relies on the error class, its cause's class and `errno`, and Redis' own logs.
  Other client faults lose their message for the same reason.
- A client-side storage error in an ops route is still a 500, now without its
  text; diagnosing it relies on the class.
- What would break the guarantee: building a client from one of these settings
  without the check above; logging a Redis or storage client error object or its
  traceback; calling the storage client outside `_storage_request`; catching
  broader classes in a check than the library raises for bad input.
- **Behaviour change**: the checks run when the ops routes build the store,
  which both routes do before anything else. An endpoint with a query, a
  non-numeric port or a scheme other than `http`/`https`, a bucket name the
  client rejects, or a static credential holding a control character now refuses
  every ops purge and takedown approval with a 409, including actions that never
  reach the bucket: approving a takedown for an episode with no retained object
  (200 before; now 409 and the request stays pending) and purging a transcript
  that does not exist (404 before; now 409). Such a configuration failed every
  bucket request before, so only actions that needed no bucket request change.
  Tests pin both cases.

Known limits:

- A server's error response (`ClientError`) propagates unchanged, and its
  message is the server's text. Its code and operation are fixed, but some
  servers' messages name configuration: an access-denied message can name the
  calling principal and the bucket. It is not caught by the ops routes (500,
  opaque body) and is printed in the server's error record.
- A Redis option the connection class accepts and uses only on a live connection
  is not checked at start-up: for example `socket_keepalive_options`,
  `socket_type`, `credential_provider` or `retry` given as text fail on the
  first request with a value-free error, which fails the request by class only,
  and a non-standard `encoding_errors` handler fails only if a value needs it.
- Third-party DEBUG records keep 0010's URL rule: the storage client's request
  lines print the endpoint's host.
