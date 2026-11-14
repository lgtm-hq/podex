# 0003. Bounded public feed HTTP boundary

- Status: proposed
- Date: 2026-11-14
- Tags: ingestion, security, http

## Context

RSS parsing previously fetched configured URLs inside feedparser, without a
request deadline, response limit or destination policy. Configuration and
redirects could reach private services or stall recurring discovery work.
Transcript providers will also need an injectable HTTP byte boundary where
this contract fits; unrelated enrichment clients have separate contracts.

## Decision

Fetch bytes before parsing through `FeedHttpClient`, injected through the
`FeedFetcher` protocol. Pass an in-memory byte stream to feedparser so it
cannot reinterpret a response body as a local filename. Accept only HTTP(S) URLs without user information and
public unicast IPv4/IPv6 destinations. Validate every DNS answer and redirect,
then connect to a validated numeric IP while retaining the original HTTP Host
and TLS SNI/certificate hostname. Reject IP transition mechanisms and scoped
addresses. Ignore proxy environment variables, disable automatic redirects,
and keep no cross-request cookies. Use the HTTPX transport directly so its
URL-bearing request log cannot expose query credentials.

One cancellable deadline covers DNS, connections, headers, redirects and body
streaming. Connect/read timeouts also apply. Bound both wire bytes and decoded
bytes; support identity and a single gzip member, with bounded decompression.
Limits live in nested `feed_http` settings (ADR 0001), with finite positive
values and a finite redirect count. No unlimited fallback exists.

System DNS cannot be cancelled. Run it in at most four daemon threads, keeping
capacity occupied until abandoned resolution finishes. Reject excess work
rather than queue indefinitely, and never join resolver threads on deadline
expiry. Inject DNS and HTTP transports in tests to avoid network requests.

## Consequences

Both RSS discovery paths retain their parser and normalization behavior while
fetch errors expose only bounded diagnostic text. Existing transcript provider
work has no matching HTTP byte-fetch implementation yet; future integrations
can consume this boundary without changing unrelated enrichment clients.

Inventory configured feed destinations before rollout. Private feeds, proxy-only
networks, URL user information, unsupported content encodings and concatenated
gzip members fail explicitly. Whether internal feeds need exceptions remains
open; no allowlist configuration or entries are introduced. Any exception needs
a separately specified narrow policy. No database migration or backfill is
required. Rollback must pause affected fetch jobs rather than restore an
unbounded production path.
