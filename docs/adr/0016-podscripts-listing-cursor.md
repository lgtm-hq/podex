<!-- markdownlint-disable MD041 -->

# 0016. Catalog as the podscripts listing cursor after a complete walk

- Status: proposed
- Date: 2026-11-17
- Tags: discovery, scheduler

## Context

Discovery reads a podcast's podscripts.co listing page by page. The pages carry
no publication dates, so the `since` cut-off cannot be applied while paging, and
every scheduled run read up to 200 pages per podcast (#866).

Stopping at the first listing page whose episodes the catalog already holds
saves those requests, but it assumes the catalog is complete below that page.
Nothing guaranteed that:

- A page that failed to load was treated as the end of the listing, so a partial
  run stored only the newer episodes and reported no error. Later runs would
  then stop above the gap and never fill it.
- Catalog rows can match a listing entry by episode number while holding another
  source's URL (for example a feed link). Such a row does not show that the
  listing entry itself was ever read.
- A transcript added later for an older episode appears deep in the listing,
  below pages that are already known.
- Listing order is only evidenced by the numbers some slugs start with.

## Decision

1. **Failed pages are errors.** A listing page that cannot be fetched ends the
   walk, is reported in the discovery run's `errors` (so the run is recorded as
   failed) and never counts as the end of the listing.
2. **The catalog becomes the cursor only after a recorded complete walk.**
   `podcasts.podscripts_listing_walked_at` (nullable, migration 0024) is set to
   the run's start time when a walk reached the listing's real end (an empty
   page) with no failed page, no early stop and below the page limit. It is
   written after every episode of that run is stored and committed with them;
   that holds on the success path. If storing raises part-way, the record is
   cleared instead, and the scheduled runner, which records the failure and
   commits the session at the end of the tick, commits the cleared record
   together with whatever episodes were stored, so the next run reads the whole
   listing. A complete walk is also not recorded when it saw no episode while
   the catalog has episodes, or saw fewer distinct listing URLs than the catalog
   already stores under this podcast's listing. While the record is unset,
   discovery reads the whole listing as before. A record dated after the current
   time counts as unset. The record is cleared when a run hits a failed page
   (that run may have left a gap below known pages) and when the podcast's
   podscripts slug changes through the ops update; setting it to NULL by hand
   forces the next run to read the whole listing.
3. **Stopping matches by listing URL only.** A page counts as known only when
   every listing URL on it is already stored for this podcast. Matching by
   episode number still prevents duplicate rows on save, as before, but never
   ends paging.
4. **Periodic full walk.** When the last complete walk is at least
   `PODEX_DISCOVERY__PODSCRIPTS_FULL_WALK_INTERVAL_HOURS` old (default 168, one
   week), the next run reads the whole listing and, if it completes, refreshes
   the record. Late insertions are found within that interval.
5. **Order evidence is a second guard.** A fully known page ends paging only
   when the episode numbers read so far have fallen at least twice and never
   risen. Otherwise the walk continues to the end, bounded by
   `MAX_LISTING_PAGES` (200).
6. **The `since` window is inclusive.** Every provider keeps episodes dated at
   or after `since`, through one shared helper (`published_in_window`).
   Day-granular dates sit at midnight UTC, so a strict boundary would drop a
   same-day sibling, and the upsert is idempotent, so the inclusive side is
   safe. Undated episodes take the date of the catalog row they match; with no
   date anywhere they are kept. Catalog rows outside the window are still
   enriched with fields they lack; only creation and the returned episodes are
   limited to the window.

## Consequences

- A podcast pays a full walk on its first run after deployment, after any failed
  page or failed store, after a slug change and once per interval. Other runs
  stop at the first fully known page only where the conditions above hold; see
  the gaps below.
- A failing listing page now fails the scheduled discovery run instead of
  passing silently.
- Not covered, knowingly:
  - A page that loads but has lost its episode links (a layout change or a soft
    error page) still reads as the end of the listing. The walk is not recorded
    when that leaves it short of the catalog (no episode while the catalog has
    some, or fewer listing URLs than the catalog stores), but a soft-empty page
    below everything the catalog holds, for example on a podcast's first walk,
    can still record a complete walk.
  - Shows whose listing links carry no leading number never show an order, so
    they never stop early and read the whole listing on every run (bounded by
    `MAX_LISTING_PAGES`). Safe, but no saving; tracked in #895.
  - Episodes first stored from the feed keep their feed URL, so their listing
    URL is never recorded and pages holding them are never fully known; they are
    re-read on every run until they scroll past the first fully known page.
    Safe, but no saving; tracked in #895.
  - Listings longer than `MAX_LISTING_PAGES` never complete, so they never use
    the cursor and keep reading 200 pages per run.
  - Episode numbers come from the leading digits of the slug, the only numbering
    the provider's links expose in the data this repository has. A title that
    starts with digits can still pass as a number; the complete-walk record (2)
    is the primary guard.
  - Late insertions are found only at the next full walk, up to one interval
    later.
- Changing the provider, its rate limits, or the scheduler cadence is out of
  scope.
