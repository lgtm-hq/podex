<!-- markdownlint-disable MD041 -->

# 0019. Precedence of episode identity evidence in discovery

- Status: proposed
- Date: 2026-11-17
- Tags: discovery, catalog

## Context

Discovery reads one podcast from several sources (the podscripts.co listing,
the RSS feed, Spotify) and must store each episode once. Two places decide
whether two records are the same episode:

- the in-run matcher, which groups the records of one discovery run before they
  are stored (`podex.services.episode_matching.group_same_episodes`);
- the catalog lookup, which finds the stored row for each grouped record
  (`DiscoveryOrchestrator._find_existing_episode`).

The evidence available is uneven:

- **Stable identifiers.** A feed GUID, a Spotify URI and a YouTube id are
  assigned by their source and do not change when the source renumbers or
  retitles an episode.
- **Episode numbers.** Sources number differently. A feed may number per
  season, the transcript listing across the whole show, and the listing's
  number is the leading digits of a link, which can be a title word ("10 Things
  We Learned") or a year ("2026 Year In Review"). Feeds also write 0 as a
  placeholder.
- **Episode URLs.** A listing URL is unique per episode, but a feed item's link
  can be the show's homepage for every item, so a URL is not stable.
- **Titles and dates.** Episodes of a numbered series have near-identical titles
  ("Protect Our Parks 17" and "Protect Our Parks 18"), while one episode can be
  titled slightly differently by two sources. Dates differ by hours or days
  between sources and are missing from the listing.

The first matcher let a 95% title similarity merge differently numbered
episodes (#894). Applying "differing numbers never match" literally instead
created duplicate rows: a renumbered feed item, or a row holding one source's
number and another source's GUID, got a second row with the same GUID.

`Episode` has no unique constraint on `rss_guid`, `spotify_uri`, `youtube_id`,
`episode_url` or `episode_number`. These rules are the only thing that keeps
one episode in one row; a wrong "different" verdict inserts a duplicate rather
than failing.

## Decision

**Episode number 0 means no number** everywhere: in the in-run matcher, in the
catalog lookup (on the discovered record and on stored rows) and when merging
records.

**In-run matcher.** For two discovered records, the first rule that speaks
decides:

1. A shared stable identifier (`rss_guid`, `spotify_uri`, `youtube_id`) means
   one episode, whatever the numbers, URLs or titles say.
2. Both numbers known: an equal number means one episode, even when the titles'
   numbers conflict. A differing number means two episodes only when both
   records come from the same source (`DiscoveredEpisode.discovery_source`, set
   by every provider; a record without a source never counts as the same
   source). Across sources a differing number decides nothing.
3. A shared `episode_url` means one episode.
4. Titles and dates decide:
   - Titles are compared after folding case, removing thousands separators
     ("1,000th" reads as "1000th") and reading two-digit years as four-digit
     years by the `%y` convention ("'25" as 2025, "'99" as 1999).
   - A leading numbering label is split off: a season label ("S2E5", "Season 2
     Episode 5") or an episode label ("Episode 105", "Ep. 105", "#105",
     "105." or "105 -").
   - The titles' numbers conflict when labels of one scheme carry different
     numbers, or when neither title's other numbers are all contained in the
     other's, counting repeats. Labels of two schemes are not compared. A title
     with only one label counts its label numbers with the rest. A conflict
     means two episodes.
   - Known dates more than 7 days apart mean two episodes.
   - Otherwise the title similarity must reach 80 when both dates fall on the
     same day, 90 when both are known and within 7 days, and 95 when either is
     missing. When both titles carry a label and both have text after it, the
     text after the label is compared: always for labels of one scheme, and for
     labels of two schemes ("Episode 105: X" and "S2E5: X") only when both
     dates fall on the same day. Otherwise the whole titles are compared.

Records are grouped in discovery order: each group takes every later record that
matches its first record and is not known to differ from any record already in
it. "Known to differ" is rule 1 to 3 when they speak, otherwise a conflict in
the titles' numbers.

**Catalog lookup.** A stored row's number does not record which source gave it,
so the lookup cannot apply the same-source condition. For each grouped record,
in order:

1. A row with the same `youtube_id`, `spotify_uri` or `rss_guid`, whatever its
   number.
2. A row with the same known episode number.
3. A row with the same `episode_url` whose number is unknown (NULL or 0) or
   equal; a URL never overrides a differing number.

Titles and dates are not compared against the catalog. When a matched row's
number differs from the incoming one, the stored number is kept: discovery
fills only missing source identifiers, URL, duration, thumbnail and date, and
never writes `episode_number` on an existing row.

## Consequences

- One episode reported by two sources that number it differently is stored once
  when both records are seen in the same run, and when a later run reads both:
  the merged record carries the earlier row's GUID, listing URL or number.
- A renumbered feed item keeps its row through its GUID, so a listing URL is
  never stored on two rows by renumbering, and the listing cursor's complete
  walk (ADR 0016) is still recorded.
- Not covered, knowingly:
  - Records seen in different runs with nothing in common: a run that reads
    only the feed stores `{number 5, GUID g, feed link}`, and a later run that
    reads only the listing stores `{number 105, listing URL}` as a second row
    (and the same in the other order). Only a title match against the catalog
    could join them; this decision does not add one.
  - The catalog lookup matches equal numbers across sources: a listing link
    "10-things-we-learned" (number 10) finds a feed's episode 10 if that is
    stored first.
  - Number words ("Part One", "Part Two"), roman numerals ("Part II") and
    "Side A" / "Side B" are not read as numbers, so such siblings are kept apart
    only by their identifiers, numbers or dates.
  - Equal episode numbers in different seasons of one feed are one episode,
    because the feed reader does not read the season; tracked in #905. A feed
    whose items all link to the homepage joins unnumbered items through rule 3;
    also #905.
  - Grouping depends on discovery order: a record joins the first group whose
    first record it matches, so a different order can group borderline records
    differently.
  - Two-digit years follow the `%y` pivot, so "'69" reads as 1969.
- Changing a threshold, the label patterns or the precedence changes which rows
  are created; any change should come with a new ADR and tests that pin the
  pairs on each side of the change.
