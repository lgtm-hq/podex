"""Decide whether two discovered episode records describe the same episode.

Discovery reads one episode from several sources and must store it once, while
episodes of a numbered series ("Protect Our Parks 17", "Protect Our Parks 18")
carry nearly identical titles and must stay apart. ADR 0018 records the rule;
in order:

1. A shared stable identifier (feed GUID, Spotify URI, YouTube id) means one
   episode, whatever the numbers or titles say.
2. Known episode numbers decide next. Episode number 0 means no number. An
   equal number means one episode. A differing number means two episodes only
   when both records come from the same source; sources number differently,
   so across sources a differing number decides nothing.
3. A shared episode URL means one episode.
4. Otherwise the titles and dates decide. Conflicting numbers written in the
   titles mean two episodes, as do publication dates more than
   ``DATE_TOLERANCE`` apart. Without a conflict, similar titles mean one
   episode; how similar depends on how close the known dates are.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta

from rapidfuzz import fuzz

from podex.services.discovery import DiscoveredEpisode

STABLE_ID_FIELDS = ("youtube_id", "spotify_uri", "rss_guid")
"""Fields whose equal values identify one episode whatever else differs."""

DATE_TOLERANCE = timedelta(days=7)
"""Largest gap between two known publication dates of one episode."""

SAME_DAY_TITLE_RATIO = 80
"""Title similarity that suffices when both dates fall on the same day."""

NEAR_DATE_TITLE_RATIO = 90
"""Title similarity that suffices when both dates are within the tolerance."""

UNDATED_TITLE_RATIO = 95
"""Title similarity that suffices when either date is unknown."""

SHORT_YEAR_PIVOT = 69
"""Two-digit years below this are read as 20xx, the rest as 19xx (``%y``)."""

_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_SHORT_YEAR = re.compile(r"['’](\d{2})(?!\d)")
_LABEL_END = r"[\s:.|)\-\u2013\u2014]*"
_SEASON_LABEL = re.compile(
    r"^(?:s|season\s*)(\d+)[\s,.:-]*(?:e|ep\.?|episode\s*)(\d+)(?!\d)" + _LABEL_END,
)
_EPISODE_LABEL = re.compile(
    r"^(?:(?:episode|ep\.?|#)\s*(\d+)(?!\d)|(\d+)\s*[.:)|\-\u2013\u2014](?!\d))"
    + _LABEL_END,
)
_NUMBER = re.compile(r"\d+")


@dataclass(frozen=True, slots=True)
class _Title:
    """A title split into its numbering label, if any, and the rest."""

    text: str
    label_kind: str | None
    label: tuple[int, ...]
    body: str

    @property
    def body_numbers(self) -> Counter[int]:
        """Return the numbers written in the title outside its label."""
        return Counter(int(digits) for digits in _NUMBER.findall(self.body))


def _expand_short_year(match: re.Match[str]) -> str:
    """Return the four-digit year for a two-digit year such as ``'25``."""
    year = int(match.group(1))
    century = 2000 if year < SHORT_YEAR_PIVOT else 1900
    return str(century + year)


def _parse_title(title: str) -> _Title:
    """Normalise a title and split off a leading numbering label.

    Case is folded, thousands separators are removed ("1,000" reads as
    "1000") and two-digit years (``'25``) become four-digit years. A leading
    "S2E5" or "Season 2 Episode 5" is a season label; a leading "Episode 105",
    "Ep. 105", "#105" or "105." / "105 -" is an episode label.
    """
    text = title.casefold().strip()
    text = _THOUSANDS_SEPARATOR.sub("", text)
    text = _SHORT_YEAR.sub(_expand_short_year, text)
    season = _SEASON_LABEL.match(text)
    if season:
        return _Title(
            text=text,
            label_kind="season",
            label=(int(season.group(1)), int(season.group(2))),
            body=text[season.end() :],
        )
    episode = _EPISODE_LABEL.match(text)
    if episode:
        return _Title(
            text=text,
            label_kind="episode",
            label=(int(episode.group(1) or episode.group(2)),),
            body=text[episode.end() :],
        )
    return _Title(text=text, label_kind=None, label=(), body=text)


def known_number(number: int | None) -> int | None:
    """Return the episode number, or None when it is unknown or 0.

    Feeds write 0 as a placeholder, so 0 never identifies an episode.

    Args:
        number: An episode number as reported.

    Returns:
        The number, or None for a missing or zero number.
    """
    return number or None


def title_numbers_conflict(first: str, second: str) -> bool:
    """Return whether the numbers written in two titles rule out one episode.

    Labels of one scheme ("Episode 105" and "#106", or "S2E5" and "S2E6")
    conflict when their numbers differ; labels of two schemes are not
    compared. The other numbers conflict when neither title's numbers are all
    contained in the other's, counting repeats, so "Part 1" and "Part 2"
    conflict while "Protect Our Parks 18" and "#2581 - Protect Our Parks 18"
    do not. A title without numbers never conflicts.

    Args:
        first: One episode title.
        second: The other episode title.

    Returns:
        True when the titles name different numbers.
    """
    one, other = _parse_title(first), _parse_title(second)
    one_numbers, other_numbers = one.body_numbers, other.body_numbers
    if one.label_kind is not None and other.label_kind is not None:
        if one.label_kind == other.label_kind and one.label != other.label:
            return True
    else:
        one_numbers.update(one.label)
        other_numbers.update(other.label)
    return not (one_numbers <= other_numbers or other_numbers <= one_numbers)


def _shared_stable_identifier(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> bool:
    """Return whether the records share a feed GUID, Spotify URI or YouTube id."""
    return any(
        getattr(first, name) and getattr(first, name) == getattr(second, name)
        for name in STABLE_ID_FIELDS
    )


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
    first_number = known_number(first.episode_number)
    second_number = known_number(second.episode_number)
    if first_number is not None and second_number is not None:
        if first_number == second_number:
            return True
        if _same_source(first, second):
            return False
    if first.episode_url and first.episode_url == second.episode_url:
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
    one: _Title,
    other: _Title,
    *,
    same_day: bool,
) -> tuple[str, str]:
    """Return the parts of two titles whose similarity is measured.

    The text after the labels is compared when both titles carry a label and
    text after it, for labels of one scheme always and for labels of two
    schemes only on the same day. Otherwise the whole titles are compared.
    """
    if one.label_kind is None or other.label_kind is None:
        return one.text, other.text
    if not (one.body and other.body):
        return one.text, other.text
    if one.label_kind != other.label_kind and not same_day:
        return one.text, other.text
    return one.body, other.body


def _titles_and_dates_match(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> bool:
    """Return whether similar titles and compatible dates show one episode.

    When both titles carry a numbering label and the rest of each title is
    not empty, the rest is compared: always for labels of one scheme (their
    numbers already agree), and for labels of two schemes only when both
    dates fall on the same day. Otherwise the whole titles are compared.
    """
    if title_numbers_conflict(first.title, second.title) or _dates_conflict(
        first,
        second,
    ):
        return False
    one_text, other_text = _compared_texts(
        _parse_title(first.title),
        _parse_title(second.title),
        same_day=_same_day(first, second),
    )
    title_ratio = fuzz.ratio(one_text, other_text)
    if first.published_at is None or second.published_at is None:
        return bool(title_ratio >= UNDATED_TITLE_RATIO)
    if _same_day(first, second):
        return bool(title_ratio >= SAME_DAY_TITLE_RATIO)
    return bool(title_ratio >= NEAR_DATE_TITLE_RATIO)


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


def group_same_episodes(
    episodes: list[DiscoveredEpisode],
) -> list[list[DiscoveredEpisode]]:
    """Group discovered records that describe the same episode, in input order.

    Each group starts at the first record not yet grouped and takes every later
    record that matches it and is not known to differ from a record already in
    the group, so two numbered siblings never meet through a third record.

    Args:
        episodes: Discovered records from every source, in discovery order.

    Returns:
        Groups of records, each describing one episode.
    """
    groups: list[list[DiscoveredEpisode]] = []
    used: set[int] = set()
    for index, anchor in enumerate(episodes):
        if index in used:
            continue
        used.add(index)
        group = [anchor]
        for other_index in range(index + 1, len(episodes)):
            if other_index in used:
                continue
            candidate = episodes[other_index]
            if episodes_match(anchor, candidate) and not any(
                known_different(member, candidate) for member in group
            ):
                group.append(candidate)
                used.add(other_index)
        groups.append(group)
    return groups
