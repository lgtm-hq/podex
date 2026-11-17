<!-- markdownlint-disable MD041 -->

# 0019. Precedence of episode identity evidence in discovery

- Status: proposed
- Date: 2026-11-17
- Tags: discovery, catalog

## Context

Discovery reads one podcast from several sources: the podscripts.co listing, the
RSS feed and Spotify. It must store each episode once. Two places decide whether
records are the same episode:

- the **in-run matcher**, which groups the records of one discovery run before
  they are stored (`podex.services.episode_matching.group_same_episodes`);
- the **catalog lookup**, which finds the stored row for each grouped record
  (`DiscoveryOrchestrator._find_existing_episode`).

The in-run matcher let a 95% title similarity merge consecutive episodes of a
numbered series ("Protect Our Parks 17" and "Protect Our Parks 18"), so one of
them was never stored (#894). The evidence it can use is uneven:

- **Stable identifiers.** A feed GUID, a Spotify URI and a YouTube id are
  assigned by their source.
- **Episode numbers.** Sources number differently. A feed may number per
  season (`itunes:season`), while the listing numbers across the show. The listing's number is
  the leading digits of a link, which can be a title word ("10 Things We
  Learned") or a year ("2026 Year In Review"). Feeds write 0 as a placeholder.
- **Episode URLs.** A listing URL is unique per episode; a feed item's link may
  be the show's homepage.
- **Feed GUIDs.** Most feeds give each item its own GUID, but some reuse one
  GUID for every item or use the homepage as the GUID, and a feed may change
  the letter case of its GUIDs.
- **Titles and dates.** Titles of a numbered series are near-identical, one
  episode can be titled slightly differently by two sources, and the listing
  carries no dates.

Changing the catalog lookup as well created duplicate rows that the earlier
lookup avoided, so this decision first left the lookup as it was. It changes
it only where the feed inputs below require it (#905).

`Episode` has no unique constraint on `rss_guid`, `spotify_uri`, `youtube_id`,
`episode_url` or `episode_number`. These rules are the only thing that keeps
one episode in one row; a wrong verdict inserts a duplicate or merges two
episodes rather than failing.

## Decision

### Feed reader inputs

The feed reader (`podex.services.discovery_rss`) reads the whole feed before it
reports any item, so that it passes on only values that name one item:

- **Links.** An item's `<link>` becomes its `episode_url` unless it equals the
  feed's own site link (the channel `<link>`) or another item of the feed has
  the same link. Links are compared ignoring surrounding whitespace, the case
  of the scheme and host, a trailing slash and the fragment
  (`discovery_rss.link_key`); the reported URL is the link as written.
- **GUIDs.** An item's GUID becomes its `rss_guid` unless another item of the
  feed has the same GUID (by the GUID rule below), or the GUID, read as a
  link, is the site link or a link shared by items.
- **Seasons.** A positive whole `itunes:season` is carried as
  `DiscoveredEpisode.season_number`; a missing, 0 or unreadable season means
  none. It qualifies the item's episode number.

Items are counted across the whole feed, before the `since` window is applied.

### Comparing GUIDs

Two GUIDs are the same when they are equal after removing surrounding
whitespace and lowercasing the ASCII letters A to Z; every other character,
including non-ASCII letters, compares exactly (`episode_matching.guid_key`). An
empty or blank GUID identifies nothing. The in-run matcher, the feed reader's
count of shared GUIDs and the catalog lookup all use this rule. Stored GUIDs are
never rewritten; a new row stores the GUID as the feed wrote it.

### In-run matcher

For two records of one run, the first rule that speaks decides:

1. A shared stable identifier (`rss_guid` by the GUID rule, `spotify_uri` and
   `youtube_id` exactly) means one episode. An empty or blank value identifies
   nothing.
2. Two different GUIDs on records of the same source mean two episodes. The
   feed reader drops a GUID that several items share, so a GUID that reaches
   the matcher names one item, and two items of one feed with their own GUIDs
   are never merged by number, URL, title or date.
3. A differing episode number means two episodes only when both records come
   from the same source (`DiscoveredEpisode.discovery_source`, set by every
   provider). A record without a source is never the same source as another
   record, including another record without one. Across sources a differing
   number decides nothing. Numbers in two known, different seasons differ even
   when they are equal; a record without a season (the listing numbers across
   the show) is compatible with any season.
4. An equal episode number (in the same season, or where a season is unknown)
   means one episode, even when the numbers written in the titles conflict.
5. A shared `episode_url` means one episode. This keeps the earlier behaviour
   for a listing URL seen twice in one run; it ranks below the numbers.
6. Titles and dates decide:
   - Conflicting numbers written in the titles mean two episodes, as do known
     dates more than 7 days apart.
   - Otherwise the lowercased titles, the same text compared before this
     decision, must reach a similarity of 80 when both dates fall on the same
     day, 90 when both are known and within 7 days, and 95 when either is
     missing.
   - There is one exception to comparing whole titles. When both titles start
     with numbering labels of two schemes (an episode label such as "Episode
     105", "Ep. 105", "#105", "105." or "105 -", and a season label such as
     "S2E5" or "Season 2 Episode 5"), both have text after the label, the
     records come from two sources and both dates fall on the same day, the
     text after the labels is compared.

Episode number 0 means no number in the matcher, and season 0 means no season.

**Title numbers.** Title numbers are read only to detect conflicts; they never
change the similarity score.

- Labels of one scheme conflict when their numbers differ. Labels of two
  schemes are not compared. A label on one title only counts with that title's
  other numbers.
- The other numbers conflict when, under every reading, neither title's
  numbers are all contained in the other's, counting repeats.
- A comma followed by exactly three digits, with no further digit after them,
  may be a thousands separator. Such a number is read both joined ("1,000" as
  1000) and as a list ("100,101" as 100 and 101).
- A quoted two-digit number ("'25", "'90s") is a short year. It is compatible
  with the same bare number (25) and with its full year, using the `%y` pivot
  ('68 is 2068, '69 is 1969).

**Grouping.** Matching pairs join their groups in order of strength:

1. a shared stable identifier or URL;
2. an equal number whose titles and dates also match;
3. a title-and-date match;
4. an equal number alone.

Pairs of one strength join in discovery order. Two groups join only when no
record of one is known to differ from a record of the other. "Known to differ"
means rules 2 and 3 above, or, when rules 1 to 5 are silent, conflicting title
numbers.

An equal number therefore always pairs before a title match when its titles
match too. An equal number whose titles disagree yields to title matches. For
example, the listing's "10-things-we-learned" (number 10) does not take the
feed's "The Tenth One" (number 10) away from the listing's "the-tenth-one".

When a group is merged into one record, the merged record keeps the season of a
record whose number equals the merged number, so the feed's season never
qualifies the listing's number.

### Catalog lookup

For each grouped record the catalog lookup returns the first stored row of the
podcast with, in order:

1. the same `youtube_id`;
2. the same `spotify_uri`;
3. the same `rss_guid` by the GUID rule: a row with the GUID exactly as
   written first, then a row whose GUID differs only in surrounding whitespace
   or ASCII letter case; a blank GUID matches nothing;
4. the same `episode_url`;
5. the same episode number, when the record's number is not 0 or missing.
   When the record carries a season, a row of that season comes first, then a
   row without a season, never a row of another season
   (`episodes.season_number`, migration 0026, is NULL for rows stored before
   it and for rows of sources without seasons). A record without a season
   matches any row with the number, as before;
6. only for a record with no YouTube id, Spotify URI, GUID or URL: the same
   title and the same publication time. This keeps feed items whose link and
   GUID the reader no longer passes on from being stored again on every run.

Apart from the GUID rule in step 3, the season in step 5 and step 6, the lookup
is as it was. The feed reader no longer emits homepage links or shared GUIDs, so
steps 3 and 4 no longer match through them. The lookup compares no titles or
dates except in step 6. A matched row only gains the fields it lacks:
identifiers, URL, duration, thumbnail and date. Its title, episode number and
season are never rewritten, and a season is stored only when a row is created.
Stored rows numbered 0 are therefore never found by number.

Zero means no number in the matcher and in this lookup, but not everywhere. The
listing cursor's order evidence (ADR 0016) still reads a `0-` slug as number 0.
That is harmless: the order evidence only decides whether a page whose URLs are
all already stored may end the walk.

## Consequences

- Episodes of a numbered series are stored separately whichever sources report
  them. One episode numbered differently by the listing and the feed is stored
  once when both are read in one run.
- Season 1 episode 5 and season 2 episode 5 of one feed are two rows. Items
  linking to the homepage or sharing a GUID are separate rows, and no item's
  GUID lands on another item's row. Items of one feed with their own GUIDs are
  never merged by title. A GUID that changes case finds its row. A feed with
  unique links and GUIDs and no seasons is read and stored as before.
- A listing numbered across the show and a feed numbered per season pair as
  before: the listing has no season, so an equal number still pairs (in one
  run and in the lookup), and a differing number still leaves it to the titles.
  The season only separates two records that both carry one.
- Tests pin one row per listing URL, and the complete-walk record of ADR 0016
  set on later runs, for these cases:
  - a feed-first row without a link, filled with the listing URL and then
    reported by the listing alone;
  - a feed without GUIDs renumbering an item;
  - a feed changing an item's GUID and number together;
  - a listing whose slug number equals another item's feed number.

  These tests do not cover the case below where the feed row keeps its own
  link.
- Not covered, knowingly:
  - An episode first stored from the feed keeps the feed's URL and number. A
    later run reading both sources matches it by GUID but never stores the
    listing's URL or number on the row. When the item leaves the feed, or the
    feed fails for one run, the listing record becomes a second row, and a run
    reading both sources does not heal this. (Same on main; tracked in #895.)
    The exception is a feed row without a URL, which gains the listing's URL.
  - A listing numbered across the show, paired with a feed numbered per season,
    pairs by order when titles are identical. An oldest-first feed can then
    cross two episodes.
  - Rows merged before this change are not split. Such a row may hold the
    homepage as its URL, or a GUID that several items share; the next run
    stores the other items as rows of their own, and the merged row keeps the
    first item. To find such rows, look for URLs that are a site root
    (`episode_url ~* '^https?://[^/?#]+/?$'` on PostgreSQL) or equal the feed's
    channel link, and for stored GUIDs of a feed that the current reader no
    longer reports for any item. Rows of one podcast whose GUIDs differ only in
    case are found by grouping on `lower(trim(rss_guid))`.
  - Rows stored before migration 0026 have no season, so a seasoned record can
    still find such a row of another season by number; rows stored afterwards
    carry their season.
  - Items of a feed without GUIDs that share a title and fall within seven days
    still merge in one run; only distinct GUIDs keep same-source items apart.
  - Step 6 of the lookup needs the exact title and publication time; an item
    that is retitled or redated while it carries no identifier or URL is
    stored again.
  - Number words ("Part One"), roman numerals ("Part II") and "Side A" / "Side
    B" are not read as numbers.
  - Groups join transitively. Two records that do not match each other can
    share a group through a third when nothing marks them as different.
- Changing a threshold, the label patterns, the tiers or the precedence changes
  which rows are created. Any such change needs a new ADR and tests that pin the
  pairs on each side of the change.
