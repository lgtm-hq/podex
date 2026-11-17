<!-- markdownlint-disable MD041 -->

# 0020. Feed identity inputs, seasons and the catalog lookup

- Status: proposed
- Date: 2026-11-17
- Tags: discovery, catalog

## Context

ADR 0019 sets the precedence of identity evidence in the in-run matcher and
records the catalog lookup as it stood. Its inputs from the RSS feed were
misleading (#905):

- The feed reader read only `itunes:episode`. Season 1 episode 5 and season 2
  episode 5 were matched by number and stored as one row.
- The item `<link>` became the episode URL even when every item linked to the
  show's homepage. Such items merged within a run and across runs, and a later
  item's GUID was written onto the row of another.
- A GUID that every item reused, or the homepage used as a GUID, collapsed the
  feed to one row.
- Items of one feed with identical unnumbered titles and distinct GUIDs merged
  when dated within seven days ("Morning Briefing" on consecutive days).
- GUIDs that differed only in letter case were different episodes.

Fixing the inputs means some feed items reach the catalog with no identifier
or URL at all, so the catalog lookup needs a way to find them again; otherwise
they are stored again on every run.

Discovery sessions do not autoflush, so the lookup sees only rows stored by
earlier runs, never rows created earlier in the same run.

This record extends ADR 0019; it changes none of its decisions except where
stated below.

## Decision

### Feed reader inputs

The feed reader (`podex.services.discovery_rss`) reads the whole feed before it
reports any item, and passes on only values that name one item:

- **Links.** An item's `<link>` becomes its `episode_url` unless it equals the
  feed's own site link (the channel `<link>`) or another item of the feed has
  the same link. Links are compared ignoring surrounding whitespace, the case
  of the scheme and host, a trailing slash and the fragment
  (`discovery_rss.link_key`). A link that cannot be read as a URL compares as
  written. The reported URL is the link as written.
- **GUIDs.** An item's GUID becomes its `rss_guid` unless another item of the
  feed has the same GUID (by the GUID rule below), or the GUID, read as a
  link (compared as links are, so ignoring the fragment), is the site link or
  a link shared by items.
- **Seasons.** A positive whole `itunes:season` is carried as
  `DiscoveredEpisode.season_number`. A missing, 0 or unreadable season means
  none. The season qualifies the item's episode number.
- **Dates.** A parsed `pubDate` is reported in UTC (`astimezone(UTC)`).
  SQLite keeps only the wall-clock time of a stored value, so a date kept with
  its offset would be read back as another instant; in UTC, what is stored and
  what step 6 below compares are the same instant on every database.

Links and GUIDs are counted across the whole feed, before the `since` window is
applied.

### Comparing GUIDs

Two GUIDs are the same when they are equal after removing surrounding ASCII
whitespace (space, tab, line feed, carriage return, form feed, vertical tab)
and lowercasing the ASCII letters A to Z. Every other character, including
non-ASCII letters, compares exactly (`episode_matching.guid_key`). An empty or
blank GUID identifies nothing. The in-run matcher, the reader's count of shared
GUIDs and the catalog lookup use this rule. Stored GUIDs are never rewritten; a
new row stores the GUID as the feed wrote it.

### In-run matcher

ADR 0019's rules apply, with these changes:

- Rule 1 compares `rss_guid` by the GUID rule; `spotify_uri` and `youtube_id`
  still compare exactly.
- A new rule follows rule 1: two different GUIDs on records of the same source
  mean two episodes. The reader drops shared GUIDs, so a GUID that reaches the
  matcher names one item, and within one run two items of one feed with their
  own GUIDs are never merged by number, URL, title or date. It counts as
  "known to differ" when groups join.
- Episode numbers in two known, different seasons are different numbers, even
  when equal: from one source they mean two episodes, across sources they
  decide nothing. A record without a season (the listing numbers across the
  show) is compatible with any season. The grouping tiers use the same
  comparison. Season 0 means no season.
- A merged record keeps the season of a record whose number equals the merged
  number, so the feed's season never qualifies the listing's number.

### Seasons in the catalog

`episodes.season_number` (nullable, migration 0027) holds the season. It is
written when a row is created. A row without a season gains one when a record
finds it by GUID or by URL (lookup steps 3 and 4) and both carry the same
non-zero episode number. A stored season is never overwritten.

### Catalog lookup

For each grouped record the lookup returns the stored row of the podcast found
by the first step that finds one:

1. the same `youtube_id`;
2. the same `spotify_uri`;
3. the same `rss_guid` by the GUID rule: a row with the GUID exactly as
   written first, as before; otherwise the rows whose trimmed, lowercased GUID
   equals the record's key lowercased, checked again with `guid_key`, lowest
   id first. A blank GUID matches nothing;
4. the same `episode_url`;
5. the same episode number, when the record's number is not 0 or missing.
   A record with a season takes a row of that season first, then a row
   without a season, never a row of another season; it skips a row without a
   season that holds a GUID when the record has a GUID too, because step 3
   has already ruled that row out. A record without a season takes any row
   with the number, as before;
6. for a record without a YouTube id or Spotify URI: the same title and the
   same publication instant, or no publication time on both; a dated record
   never takes an undated row. For an undated record this step is therefore
   a match by title alone, among undated rows. A row whose GUID (by the GUID
   rule), URL or number (with seasons as in the matcher) contradicts the
   record is never taken. Lowest id first: several undated rows with one
   title resolve to the oldest. Within one run such records are already
   grouped by the matcher (identical titles, no dates).

Apart from the GUID rule in step 3, the season in step 5, step 6 and the season
fill, the lookup is ADR 0019's. A matched row still only gains fields it lacks;
its title and number are never rewritten.

## Consequences

- Season 1 episode 5 and season 2 episode 5 of one feed are two rows. Items
  linking to the homepage or sharing a GUID are separate rows, and no item's
  GUID lands on another item's row. Items of one feed with their own GUIDs are
  never merged by title. A GUID that changes case finds its row. Feeds with
  unique links (other than by fragment) and unique GUIDs and no seasons are
  read and stored as before.
- Feed items left without an identifier or URL are found again by title and
  time, also when undated, when the reader cannot parse their dates and when
  their dates carry a UTC offset. On a fresh catalog the first run stores
  them and later runs add nothing. On a catalog an earlier release filled, the
  row it merged the items into keeps its first item and the other items get
  rows of their own in the first run, except as listed under "not covered"
  below (wall-clock dates, a feed that fixes its GUIDs or links at the
  deploy); the count is stable after that.
- A link or GUID that stops being shared (fixed by the feed, or because the
  sharing item left the feed) finds the row stored while it was shared, and
  the row gains it.
- In a run that reads both sources, a listing numbered across the show pairs
  with a feed numbered per season as on main: the listing has no season, so an
  equal number still pairs and a differing number leaves it to the titles.
- Cost: each record that misses the exact GUID scans the podcast's rows once
  more (step 3 has no expression index), as the URL step already does
  (`episode_url` has no index). Measured on SQLite at 20,000 rows: the GUID
  step took 18 to 21 ms against 5 to 8 ms for the exact query alone; storing
  2,000 new feed items into a podcast holding 20,000 rows took 23 to 44 s on
  this change against 41 to 52 s before it, on a noisy laptop. Rows created in
  the same run are invisible to the lookup, so a first import into an empty
  podcast does not grow this cost.
- Rows merged before this change are not split. To find them: URLs that are a
  site root (`episode_url ~* '^https?://[^/?#]+/?$'` on PostgreSQL) or equal
  the feed's channel link; stored GUIDs of a feed that the current reader no
  longer reports for any item; rows of one podcast whose GUIDs differ only in
  case, grouped on `lower(trim(rss_guid))`.
- Not covered, knowingly:
  - A listing numbered across the show against a feed numbered per season,
    when a run reads one source only. Listing "17-second-wind" and
    "5-opening-night" stored first, then runs reading only the feed (season 2
    episode 5 "Second Wind" listed before season 1 episode 5 "Opening Night",
    each with its own GUID): 2, 3, 3, 3 rows, against 2, 2, 2, 2 on main. The
    feed's season 2 episode 5 takes the listing's "5-opening-night" row (its
    GUID and date, as on main) and season 1 episode 5 becomes a second
    "Opening Night" row. Feed first, then runs reading only the listing: 2, 3,
    3 rows against 1, 2, 2 on main. When the later runs read both sources the
    counts are 2, 2, 2, 2 and 2, 2, 2 (main: 2, 2, 2, 2 and 1, 1, 1). The
    lookup compares no titles in the number step, so there is no cheap fix.
  - In the lookup, two items of one feed with their own GUIDs are not always
    two rows. A later item numbered 5 with its own GUID and no season finds
    the stored item 5 by number (1, 1, 1 rows, as on main), and season 1
    episode 5, an unseasoned bonus numbered 5 and season 2 episode 5 end as
    two rows (main: one). Refusing a row that holds a different GUID in the
    number step would store a second row for a feed that changes an item's
    GUID and link together, which main keeps as one.
  - A row stored before migration 0027 gains its season only from a run that
    finds it by its GUID or URL. If the item's GUID and link both change
    before such a run, the seasoned record skips the season-less row and a
    second row is stored (main keeps one).
  - Rows an earlier release stored on SQLite from a feed dated with a UTC
    offset hold the feed's wall-clock time, read back as UTC, so step 6 never
    finds them by time. Once such an item is identified by neither its GUID
    nor its URL, its old row stays and the item gets one row of its own; the
    count is then stable (three homepage-linked items without GUIDs at -0700:
    1 row before the deploy, then 4, 4, 4, 4). Stored times are not
    reinterpreted. After the first run each such item has two rows of one
    title whose times differ by the feed's offset; on PostgreSQL:
    `SELECT a.id AS old_id, b.id AS new_id, a.title FROM episodes a JOIN
    episodes b ON b.podcast_id = a.podcast_id AND b.title = a.title AND
    b.id > a.id WHERE a.published_at <> b.published_at AND
    abs(extract(epoch FROM a.published_at - b.published_at)) <= 14 * 3600;`
    The lower id is the old row.
  - A republished episode with a new GUID becomes a second row: within a feed
    that lists both copies, and across runs when its link changes too. A feed
    whose links are not passed on (homepage links) and that regenerates every
    GUID doubles its rows: a daily show of four items gives 4, 4, 8, 8, 8, 8
    rows when the GUIDs are regenerated before the third run, where main keeps
    one row throughout. Step 6 does not take a row that holds another GUID.
  - The row an earlier release merged a feed's items into remains when the
    feed fixes its GUIDs or links at the deploy: the items then arrive with
    their own GUIDs or links, step 6 does not take a row holding the shared
    GUID or link, and every item gets a new row, the first item included.
  - A season filled on a row earlier in a run is not visible to later lookups
    in that run (no autoflush). A row stored before migration 0027 that a run
    fills by GUID can still be taken by number, in that same run, by another
    season's record without a GUID; the split arrives one run late (season 1
    episode 5 by GUID and a GUID-less season 2 episode 5 together: 1, 2, 2,
    2 rows).
  - A wrong season can become permanent on a listing row. Listing
    "5-opening-night" stored first, then runs reading only the feed's season
    2 episode 5 "Second Wind": the first run takes the listing row by number
    and gives it the feed's GUID; the next finds it by that GUID with the
    same number and fills season 2, which is never overwritten. To find such
    rows: `SELECT id, title, episode_url, episode_number, season_number FROM
    episodes WHERE season_number IS NOT NULL AND episode_url LIKE
    'https://podscripts.co/%';`, then compare each title with the feed item
    holding the row's GUID.
  - A feed of one item, or a truncated feed, cannot show that a link or GUID
    is shared; only the site link is recognised there.
  - Links unique only by their fragment ("/#ep1", "/#ep2") compare as the same
    link, so such links are treated as the site link or as shared and dropped;
    those feeds are not read exactly as before. The same holds for unique
    GUIDs that differ from the site link only by a fragment: they are
    dropped too.
  - Items of a feed without GUIDs that share a title and fall within seven
    days still merge in one run; only distinct GUIDs keep same-source items
    apart.
  - Step 6 needs the exact title and publication time; an item without an
    identifier or URL that is retitled or redated is stored again. For an
    undated record step 6 matches by title alone among undated rows.
  - Dates the reader cannot parse (ISO 8601, Atom) are stored as no date; the
    reader only parses RFC 822 dates (tracked separately).
- Changing the reader's identity inputs, the GUID rule, the season rules or
  the lookup steps changes which rows are created; such a change needs a new
  ADR and tests that pin the cases on each side of it.
