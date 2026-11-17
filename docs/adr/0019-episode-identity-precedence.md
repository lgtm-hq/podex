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
  season, while the listing numbers across the show. The listing's number is
  the leading digits of a link, which can be a title word ("10 Things We
  Learned") or a year ("2026 Year In Review"). Feeds write 0 as a placeholder.
- **Episode URLs.** A listing URL is unique per episode; a feed item's link may
  be the show's homepage.
- **Titles and dates.** Titles of a numbered series are near-identical, one
  episode can be titled slightly differently by two sources, and the listing
  carries no dates.

Changing the catalog lookup as well created duplicate rows that the earlier
lookup avoided, so this decision leaves the lookup as it was.

`Episode` has no unique constraint on `rss_guid`, `spotify_uri`, `youtube_id`,
`episode_url` or `episode_number`. These rules are the only thing that keeps
one episode in one row; a wrong verdict inserts a duplicate or merges two
episodes rather than failing.

## Decision

### In-run matcher

For two records of one run, the first rule that speaks decides:

1. A shared stable identifier (`rss_guid`, `spotify_uri`, `youtube_id`) means
   one episode. An empty or blank value identifies nothing.
2. A differing episode number means two episodes only when both records come
   from the same source (`DiscoveredEpisode.discovery_source`, set by every
   provider). A record without a source is never the same source as another
   record, including another record without one. Across sources a differing
   number decides nothing.
3. An equal episode number means one episode, even when the numbers written in
   the titles conflict.
4. A shared `episode_url` means one episode. This keeps the earlier behaviour
   for a listing URL seen twice in one run; it ranks below the numbers.
5. Titles and dates decide:
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

Episode number 0 means no number in the matcher.

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
means rule 2 above, or, when rules 1 to 4 are silent, conflicting title numbers.

An equal number therefore always pairs before a title match when its titles
match too. An equal number whose titles disagree yields to title matches. For
example, the listing's "10-things-we-learned" (number 10) does not take the
feed's "The Tenth One" (number 10) away from the listing's "the-tenth-one".

### Catalog lookup (unchanged)

The catalog lookup is not changed by this decision. For each grouped record it
returns the first stored row of the podcast with, in order:

1. the same `youtube_id`;
2. the same `spotify_uri`;
3. the same `rss_guid`;
4. the same `episode_url`;
5. the same episode number, when the record's number is not 0 or missing.

It compares no titles or dates, and puts no condition on a stored row's number.
A matched row only gains the fields it lacks: identifiers, URL, duration,
thumbnail and date. Its title and episode number are never rewritten. Stored
rows numbered 0 are therefore never found by number.

Zero means no number in the matcher and in this lookup, but not everywhere. The
listing cursor's order evidence (ADR 0016) still reads a `0-` slug as number 0.
That is harmless: the order evidence only decides whether a page whose URLs are
all already stored may end the walk.

## Consequences

- Episodes of a numbered series are stored separately whichever sources report
  them. One episode numbered differently by the listing and the feed is stored
  once when both are read in one run.
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
  - A feed reusing one GUID for every item, identical unnumbered titles within
    seven days, and GUIDs differing only in case (same on main; tracked in
    #905). Equal numbers in different seasons of one feed and items sharing a
    homepage link are also #905.
  - Number words ("Part One"), roman numerals ("Part II") and "Side A" / "Side
    B" are not read as numbers.
  - Groups join transitively. Two records that do not match each other can
    share a group through a third when nothing marks them as different.
- Changing a threshold, the label patterns, the tiers or the precedence changes
  which rows are created. Any such change needs a new ADR and tests that pin the
  pairs on each side of the change.
