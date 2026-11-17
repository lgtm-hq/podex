"""Decide which records of one discovery run describe the same episode.

Discovery reads one episode from several sources and must store it once, while
episodes of a numbered series ("Protect Our Parks 17", "Protect Our Parks 18")
carry nearly identical titles and must stay apart. ADR 0019 records the rule.
It applies only to grouping the records of one run; matching a grouped record
against stored rows is the orchestrator's catalog lookup and is not part of it.

For two records, the first rule that speaks decides:

1. A shared stable identifier (feed GUID, Spotify URI, YouTube id; never
   empty or blank) means one episode. Feed GUIDs compare by ``guid_key``.
2. Two feed GUIDs that differ, from records of the same source, mean two
   episodes. The feed reader drops a GUID that several items of a feed share,
   so a GUID that reaches the matcher names one item.
3. A differing episode number means two episodes only when both records come
   from the same source. A record without a source is never the same source
   as another record. Episode number 0 means no number. Numbers in two known,
   different seasons differ even when they are equal.
4. An equal episode number means one episode.
5. A shared episode URL means one episode.
6. Otherwise the titles and dates decide. Conflicting numbers written in the
   titles mean two episodes, as do publication dates more than
   ``DATE_TOLERANCE`` apart; without a conflict, similar titles mean one
   episode, and how similar depends on how close the known dates are.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from itertools import product

from rapidfuzz import fuzz

from podex.services.discovery import DiscoveredEpisode

STABLE_ID_FIELDS = ("youtube_id", "spotify_uri", "rss_guid")
"""Fields whose equal, non-blank values identify one episode."""

_EXACT_ID_FIELDS = ("youtube_id", "spotify_uri")
"""Stable identifiers compared exactly; feed GUIDs compare by ``guid_key``."""

_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)

DATE_TOLERANCE = timedelta(days=7)
"""Largest gap between two known publication dates of one episode."""

SAME_DAY_TITLE_RATIO = 80
"""Title similarity that suffices when both dates fall on the same day."""

NEAR_DATE_TITLE_RATIO = 90
"""Title similarity that suffices when both dates are within the tolerance."""

UNDATED_TITLE_RATIO = 95
"""Title similarity that suffices when either date is unknown."""

SHORT_YEAR_PIVOT = 69
"""Two-digit years below this read as 20xx, the rest as 19xx (``%y``)."""

_IDENTIFIER_TIER = 0
_NUMBER_AND_TITLE_TIER = 1
_TITLE_TIER = 2
_NUMBER_ONLY_TIER = 3

_LABEL_END = r"[\s:.|)\-–—]*"
_SEASON_LABEL = re.compile(
    r"^(?:s|season\s*)(\d+)[\s,.:-]*(?:e|ep\.?|episode\s*)(\d+)(?!\d)" + _LABEL_END,
)
_EPISODE_LABEL = re.compile(
    r"^(?:(?:episode|ep\.?|#)\s*(\d+)(?!\d)|(\d+)\s*[.:)|\-–—](?!\d))" + _LABEL_END,
)
_TITLE_NUMBER = re.compile(r"(['’])?(\d+(?:,\d{3})*)(?!\d)")


@dataclass(frozen=True, slots=True)
class _ShortYear:
    """A two-digit year such as "'25", read as 25 or as its full year."""

    two_digit: int
    full: int


_Number = int | _ShortYear


@dataclass(frozen=True, slots=True)
class _TitleFacts:
    """What the matcher reads from one title."""

    text: str
    label_kind: str | None
    label: tuple[int, ...]
    body: str
    readings: tuple[tuple[_Number, ...], ...]


def _short_year(two_digit: int) -> _ShortYear:
    """Return a two-digit year with its full year by the ``%y`` pivot."""
    century = 2000 if two_digit < SHORT_YEAR_PIVOT else 1900
    return _ShortYear(two_digit=two_digit, full=century + two_digit)


def _number_readings(text: str) -> tuple[tuple[_Number, ...], ...]:
    """Return the numbers written in ``text``, once per way of reading them.

    A comma between digit groups is a thousands separator only when exactly
    three digits follow it and no further digit follows those. Such a number
    is read both joined ("1,000" as 1000) and as a list ("100,101" as 100 and
    101). A quoted two-digit number ("'25") is a short year.
    """
    joined: list[_Number] = []
    split: list[_Number] = []
    for quote, digits in _TITLE_NUMBER.findall(text):
        groups = digits.split(",")
        if quote and len(groups) == 1 and len(digits) == 2:
            year = _short_year(int(digits))
            joined.append(year)
            split.append(year)
            continue
        joined.append(int("".join(groups)))
        split.extend(int(group) for group in groups)
    if joined == split:
        return (tuple(joined),)
    return (tuple(joined), tuple(split))


@lru_cache(maxsize=8192)
def _title_facts(title: str) -> _TitleFacts:
    """Split a title into its leading numbering label, if any, and the rest.

    A leading "S2E5" or "Season 2 Episode 5" is a season label; a leading
    "Episode 105", "Ep. 105", "#105" or "105." / "105 -" is an episode label.
    """
    text = title.lower()
    stripped = text.strip()
    season = _SEASON_LABEL.match(stripped)
    episode = None if season else _EPISODE_LABEL.match(stripped)
    kind: str | None = None
    label: tuple[int, ...] = ()
    body = stripped
    if season:
        kind = "season"
        label = (int(season.group(1)), int(season.group(2)))
        body = stripped[season.end() :]
    elif episode:
        kind = "episode"
        label = (int(episode.group(1) or episode.group(2)),)
        body = stripped[episode.end() :]
    return _TitleFacts(
        text=text,
        label_kind=kind,
        label=label,
        body=body,
        readings=_number_readings(body),
    )


def _resolve(numbers: tuple[_Number, ...], other: tuple[_Number, ...]) -> Counter[int]:
    """Count ``numbers``, reading a short year as its full year if ``other`` has it."""
    plain = {number for number in other if isinstance(number, int)}
    return Counter(
        (
            (number.full if number.full in plain else number.two_digit)
            if isinstance(number, _ShortYear)
            else number
        )
        for number in numbers
    )


def _readings_compatible(
    one: tuple[_Number, ...],
    other: tuple[_Number, ...],
) -> bool:
    """Return whether one reading's numbers are contained in the other's."""
    one_counts, other_counts = _resolve(one, other), _resolve(other, one)
    return one_counts <= other_counts or other_counts <= one_counts


def title_numbers_conflict(first: str, second: str) -> bool:
    """Return whether the numbers written in two titles rule out one episode.

    Labels of one scheme ("Episode 105" and "#106", or "S2E5" and "S2E6")
    conflict when their numbers differ; labels of two schemes are not
    compared, and a label on one title only counts with that title's other
    numbers. The other numbers conflict when, under every reading, neither
    title's numbers are all contained in the other's, counting repeats: "Part
    1" and "Part 2" conflict, "Protect Our Parks 18" and "#2581 - Protect Our
    Parks 18" do not. A short year ("'25") is compatible with the same bare
    number and with its full year. A title without numbers never conflicts.

    Args:
        first: One episode title.
        second: The other episode title.

    Returns:
        True when the titles name different numbers.
    """
    one, other = _title_facts(first), _title_facts(second)
    one_readings, other_readings = one.readings, other.readings
    if one.label_kind is not None and other.label_kind is not None:
        if one.label_kind == other.label_kind and one.label != other.label:
            return True
    else:
        one_readings = tuple(reading + one.label for reading in one_readings)
        other_readings = tuple(reading + other.label for reading in other_readings)
    return not any(
        _readings_compatible(one_reading, other_reading)
        for one_reading, other_reading in product(one_readings, other_readings)
    )


def known_number(number: int | None) -> int | None:
    """Return the episode number, or None when it is unknown or 0.

    Feeds write 0 as a placeholder, so 0 never identifies an episode.

    Args:
        number: An episode number as reported.

    Returns:
        The number, or None for a missing or zero number.
    """
    return number or None


def guid_key(guid: str | None) -> str | None:
    """Return the form in which a feed GUID is compared, or None for no GUID.

    Surrounding whitespace is ignored and the ASCII letters A-Z compare
    without case; every other character compares exactly. An empty or blank
    GUID identifies nothing. Stored GUIDs are never rewritten to this form.

    Args:
        guid: A feed GUID as reported or stored.

    Returns:
        The comparison key, or None for a missing, empty or blank GUID.
    """
    if guid is None:
        return None
    return guid.strip().translate(_ASCII_LOWER) or None


def _shared_value(first: str | None, second: str | None) -> bool:
    """Return whether two values are equal and not empty or blank."""
    return bool(first and first.strip() and first == second)


def _shared_stable_identifier(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> bool:
    """Return whether the records share a feed GUID, Spotify URI or YouTube id."""
    guid = guid_key(first.rss_guid)
    if guid is not None and guid == guid_key(second.rss_guid):
        return True
    return any(
        _shared_value(getattr(first, name), getattr(second, name))
        for name in _EXACT_ID_FIELDS
    )


def _distinct_feed_guids(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether two records of one source carry different feed GUIDs."""
    one, other = guid_key(first.rss_guid), guid_key(second.rss_guid)
    return (
        one is not None
        and other is not None
        and one != other
        and _same_source(first, second)
    )


def _same_number(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool | None:
    """Return whether the episode numbers are equal, or None when one is unknown.

    Numbers in two known, different seasons are not equal. A record without a
    season (the listing numbers across the show) is compatible with any season.
    """
    first_number = known_number(first.episode_number)
    second_number = known_number(second.episode_number)
    if first_number is None or second_number is None:
        return None
    first_season = known_number(first.season_number)
    second_season = known_number(second.season_number)
    if (
        first_season is not None
        and second_season is not None
        and first_season != second_season
    ):
        return False
    return first_number == second_number


def _same_source(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether both records name the same, known source."""
    return (
        first.discovery_source is not None
        and first.discovery_source == second.discovery_source
    )


def _identity(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool | None:
    """Return what identifiers, numbers and URLs say, or None when they are silent."""
    if _shared_stable_identifier(first, second):
        return True
    if _distinct_feed_guids(first, second):
        return False
    same_number = _same_number(first, second)
    if same_number is not None:
        if same_number:
            return True
        if _same_source(first, second):
            return False
    if _shared_value(first.episode_url, second.episode_url):
        return True
    return None


def _dates_conflict(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether both dates are known and further apart than the tolerance."""
    if first.published_at is None or second.published_at is None:
        return False
    return bool(abs(first.published_at - second.published_at) > DATE_TOLERANCE)


def _same_day(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether both dates are known and fall on the same day."""
    if first.published_at is None or second.published_at is None:
        return False
    return bool(first.published_at.date() == second.published_at.date())


def _compared_texts(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> tuple[str, str]:
    """Return the text of each title whose similarity is measured.

    The lowercased titles, as before this rule, except that "Episode 105: X"
    and "S2E5: X" (labels of two schemes, text after both) from two sources
    on the same day compare their text after the labels.
    """
    one, other = _title_facts(first.title), _title_facts(second.title)
    if (
        one.label_kind is None
        or other.label_kind is None
        or one.label_kind == other.label_kind
        or not (one.body and other.body)
    ):
        return one.text, other.text
    if _same_source(first, second) or not _same_day(first, second):
        return one.text, other.text
    return one.body, other.body


def _titles_and_dates_match(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> bool:
    """Return whether similar titles and compatible dates show one episode."""
    if _dates_conflict(first, second):
        return False
    title_ratio = fuzz.ratio(*_compared_texts(first, second))
    if first.published_at is None or second.published_at is None:
        threshold = UNDATED_TITLE_RATIO
    elif _same_day(first, second):
        threshold = SAME_DAY_TITLE_RATIO
    else:
        threshold = NEAR_DATE_TITLE_RATIO
    if title_ratio < threshold:
        return False
    return not title_numbers_conflict(first.title, second.title)


def known_different(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether the records name different episodes outright.

    Identifiers, numbers and URLs decide when they say anything; otherwise
    conflicting numbers in the titles decide.

    Args:
        first: One discovered episode.
        second: The other discovered episode.

    Returns:
        True when the records cannot be one episode.
    """
    identity = _identity(first, second)
    if identity is not None:
        return not identity
    return title_numbers_conflict(first.title, second.title)


def episodes_match(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether two discovered records describe the same episode.

    Args:
        first: One discovered episode.
        second: The other discovered episode.

    Returns:
        True when the records should be merged into one episode.
    """
    identity = _identity(first, second)
    if identity is not None:
        return identity
    return _titles_and_dates_match(first, second)


def _pair_tier(first: DiscoveredEpisode, second: DiscoveredEpisode) -> int | None:
    """Return how strongly two records match, strongest lowest, or None.

    A shared stable identifier or URL comes first, then an equal number whose
    titles also match, then a title match, then an equal number alone.
    """
    if _shared_stable_identifier(first, second):
        return _IDENTIFIER_TIER
    identity = _identity(first, second)
    if identity is False:
        return None
    if _same_number(first, second):
        if _titles_and_dates_match(first, second):
            return _NUMBER_AND_TITLE_TIER
        return _NUMBER_ONLY_TIER
    if identity:
        return _IDENTIFIER_TIER
    return _TITLE_TIER if _titles_and_dates_match(first, second) else None


def group_same_episodes(
    episodes: list[DiscoveredEpisode],
) -> list[list[DiscoveredEpisode]]:
    """Group the records of one run that describe the same episode.

    Matching pairs join their groups strongest first: shared identifiers and
    URLs, then equal numbers with matching titles, then title matches, then
    equal numbers alone; pairs of one strength in discovery order. Two groups
    join only when no record of one is known to differ from a record of the
    other, so two numbered siblings never meet through a third record.

    Args:
        episodes: Discovered records from every source, in discovery order.

    Returns:
        Groups of records in discovery order, each describing one episode.
    """
    pairs = sorted(
        (tier, first, second)
        for first in range(len(episodes))
        for second in range(first + 1, len(episodes))
        if (tier := _pair_tier(episodes[first], episodes[second])) is not None
    )
    group_of = list(range(len(episodes)))
    members: dict[int, list[int]] = {index: [index] for index in group_of}
    for _tier, first, second in pairs:
        one, other = sorted((group_of[first], group_of[second]))
        if one == other or any(
            known_different(episodes[left], episodes[right])
            for left in members[one]
            for right in members[other]
        ):
            continue
        moved = members.pop(other)
        for index in moved:
            group_of[index] = one
        members[one] = sorted(members[one] + moved)
    return [
        [episodes[index] for index in group] for _root, group in sorted(members.items())
    ]
