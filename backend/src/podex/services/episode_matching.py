"""Decide whether two discovered episode records describe the same episode.

Discovery reads one episode from several sources and must store it once, while
episodes of a numbered series ("Protect Our Parks 17", "Protect Our Parks 18")
carry nearly identical titles and must stay apart. The rule, in order:

1. Known episode numbers that differ always mean two episodes, whatever the
   titles, links or identifiers say.
2. A shared source identifier (YouTube id, Spotify URI, RSS GUID, episode URL)
   or an equal known episode number means one episode.
3. Otherwise only the titles and dates remain. Titles whose numbers conflict
   (neither title's numbers are contained in the other's) mean two episodes, as
   do publication dates more than ``DATE_TOLERANCE`` apart. Without such a
   conflict, similar titles mean one episode; how similar depends on how close
   the known dates are.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta

from rapidfuzz import fuzz

from podex.services.discovery import DiscoveredEpisode

SOURCE_ID_FIELDS = ("youtube_id", "spotify_uri", "rss_guid", "episode_url")
"""Fields whose equal values identify one episode across records."""

DATE_TOLERANCE = timedelta(days=7)
"""Largest gap between two known publication dates of one episode."""

SAME_DAY_TITLE_RATIO = 80
"""Title similarity that suffices when both dates fall on the same day."""

NEAR_DATE_TITLE_RATIO = 90
"""Title similarity that suffices when both dates are within the tolerance."""

UNDATED_TITLE_RATIO = 95
"""Title similarity that suffices when either date is unknown."""

_TITLE_NUMBER = re.compile(r"\d+")


def numbers_differ(first: int | None, second: int | None) -> bool:
    """Return whether two episode numbers are both known and different.

    Args:
        first: One episode number, or None when unknown.
        second: The other episode number, or None when unknown.

    Returns:
        True when both numbers are known and unequal.
    """
    return first is not None and second is not None and first != second


def title_numbers_conflict(first: str, second: str) -> bool:
    """Return whether the numbers written in two titles rule out one episode.

    The numbers conflict when neither title's numbers are all contained in the
    other's, so "Part 1" and "Part 2" conflict while "Protect Our Parks 18"
    and "#2581 - Protect Our Parks 18" do not. A title without numbers never
    conflicts.

    Args:
        first: One episode title.
        second: The other episode title.

    Returns:
        True when the titles name different numbers.
    """
    first_numbers = Counter(int(digits) for digits in _TITLE_NUMBER.findall(first))
    second_numbers = Counter(int(digits) for digits in _TITLE_NUMBER.findall(second))
    return not (first_numbers <= second_numbers or second_numbers <= first_numbers)


def same_by_identity(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether a shared identifier or an equal number links two records.

    Args:
        first: One discovered episode.
        second: The other discovered episode.

    Returns:
        True when a source identifier or the known episode number is equal.
    """
    if any(
        getattr(first, name) and getattr(first, name) == getattr(second, name)
        for name in SOURCE_ID_FIELDS
    ):
        return True
    return first.episode_number is not None and (
        first.episode_number == second.episode_number
    )


def known_different(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether the records name different episodes outright.

    Differing known numbers decide it. Otherwise, unless an identifier or the
    number links the records, conflicting numbers in the titles decide it.

    Args:
        first: One discovered episode.
        second: The other discovered episode.

    Returns:
        True when the records cannot be one episode.
    """
    if numbers_differ(first.episode_number, second.episode_number):
        return True
    if same_by_identity(first, second):
        return False
    return title_numbers_conflict(first.title, second.title)


def _titles_and_dates_match(
    first: DiscoveredEpisode,
    second: DiscoveredEpisode,
) -> bool:
    """Return whether similar titles and compatible dates show one episode."""
    title_ratio = fuzz.ratio(first.title.lower(), second.title.lower())
    if first.published_at is None or second.published_at is None:
        return bool(title_ratio >= UNDATED_TITLE_RATIO)
    if abs(first.published_at - second.published_at) > DATE_TOLERANCE:
        return False
    if first.published_at.date() == second.published_at.date():
        return bool(title_ratio >= SAME_DAY_TITLE_RATIO)
    return bool(title_ratio >= NEAR_DATE_TITLE_RATIO)


def episodes_match(first: DiscoveredEpisode, second: DiscoveredEpisode) -> bool:
    """Return whether two discovered records describe the same episode.

    Args:
        first: One discovered episode.
        second: The other discovered episode.

    Returns:
        True when the records should be merged into one episode.
    """
    if known_different(first, second):
        return False
    if same_by_identity(first, second):
        return True
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
