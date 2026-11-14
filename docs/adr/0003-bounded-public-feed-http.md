# 0003. Bounded public feed HTTP boundary

- Status: proposed
- Date: 2026-11-14
- Tags: ingestion, security, http

## Context

RSS parsing previously fetched configured URLs inside feedparser, without a
request deadline, response limit or destination policy. Configuration and
redirects could reach private services or stall recurring discovery work.
Transcript providers will also need an injectable HTTP byte boundary where this
contract fits; unrelated enrichment clients have separate contracts.

## Decision

Fetch bytes before parsing through `FeedHttpClient`, injected through the
`FeedFetcher` protocol. Return the bounded body, final URL and response
`Content-Type`. Pass an in-memory byte stream to feedparser so it cannot
reinterpret a response body as a local filename. Supply `content-type` and
`content-location` response headers to preserve HTTP charset handling and
resolve relative links and images against the final URL after redirects.

Accept only HTTP(S) URLs without user information and public unicast IPv4/IPv6
destinations. Validate every DNS answer and redirect before connecting. Try
validated numeric addresses in resolver order, moving to the next address only
on connection-establishment errors, within the same total deadline. Retain the
original HTTP Host and TLS SNI/certificate hostname on each attempt. Reject IP
transition mechanisms and scoped addresses. Refuse HTTPS-to-HTTP redirects;
HTTP-to-HTTPS and same-scheme redirects remain allowed. Ports remain
unrestricted, including nondefault ports, subject to the same public-address
validation.

Send the fixed product user agent `podex-feed-fetcher/1.0` and an RSS/Atom
`Accept` header on every hop, replacing feedparser's default request headers.
Ignore proxy environment variables, disable automatic redirects, and keep no
cross-request cookies. Use the HTTPX transport directly so its URL-bearing
request log cannot expose query credentials.

One cancellable deadline covers DNS, connections, headers, redirects and body
streaming. Connect/read timeouts also apply. Bound both wire bytes and decoded
bytes; support identity and a single gzip member, with bounded decompression.
Limits live in nested `feed_http` settings (ADR 0001), with finite positive
values and a finite redirect count. No unlimited fallback exists.

System DNS cannot be cancelled. A shared four-slot worker pool bounds daemon
resolver threads across clients and event loops. Capacity stays occupied until
abandoned resolution finishes; callers return on deadline without joining those
threads. During a resolver outage, four stuck resolutions exhaust the pool and
subsequent DNS requests fail immediately rather than queue. Slots become
available again as workers finish. Inject DNS and HTTP transports in tests to
avoid network requests.

## Consequences

Both RSS discovery paths still use feedparser and the existing normalization
code, but parsing now receives explicit base-URL and content-type headers, and
fetching uses a product user agent, validated-address fallback and downgrade
refusal. Relative URL resolution and HTTP charset interpretation depend on these
headers. Fetch errors expose only bounded diagnostic text; parse-failure logs
identify a podcast slug instead of a feed URL. Existing transcript provider work
has no matching HTTP byte-fetch implementation yet; future integrations can
consume this boundary without changing unrelated enrichment clients.

Inventory configured feed destinations before rollout. Private feeds, proxy-only
networks, URL user information, unsupported content encodings, HTTPS downgrades
and concatenated gzip members fail explicitly. Whether internal feeds need
exceptions remains open; no allowlist configuration or entries are introduced.
Any exception needs a separately specified narrow policy. No database migration
or backfill is required. Rollback must pause affected fetch jobs rather than
restore an unbounded production path.
